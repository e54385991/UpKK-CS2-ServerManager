"""One-scan hub snapshot for the cross-server operation inbox."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from services.operations.inbox_messages import (
    get_many_chunked,
    last_event_text,
    read_latest_messages,
    record_message,
)
from services.operations.inbox_types import HubInboxSnapshot, InboxRecordRow, ServerInboxSlice
from services.server_operation_history import (
    COMPLETED_RETENTION_SECONDS,
    FAILED_RETENTION_SECONDS,
    _as_datetime,
    current_id_from_stored,
    ids_from_stored,
    reconcile_retained_index,
)

ACTIVE_STATUSES = frozenset({"queued", "running"})  # keep in sync with server_operation_hub


class InboxHub(Protocol):
    _lock: Any
    _records: dict[str, dict[str, Any]]
    _current: dict[int, str]
    _pending: dict[int, list[str]]
    _failed: dict[int, list[str]]
    _completed: dict[int, list[str]]
    _events: dict[str, list[dict[str, Any]]]

    @property
    def _history_redis(self) -> Any: ...

    def _record_key(self, operation_id: str) -> str: ...

    def _current_key(self, server_id: int) -> str: ...

    def _pending_key(self, server_id: int) -> str: ...

    def _failed_key(self, server_id: int) -> str: ...

    def _completed_key(self, server_id: int) -> str: ...

    def _events_key(self, operation_id: str) -> str: ...

    async def _persist_failed(self, server_id: int) -> None: ...

    async def _persist_completed(self, server_id: int) -> None: ...


async def collect_hub_snapshot(hub: InboxHub, server_ids: Sequence[int]) -> HubInboxSnapshot:
    wanted = [int(server_id) for server_id in server_ids]
    if not wanted:
        return HubInboxSnapshot(slices={}, memory_scans=0)
    memory = await _scan_memory(hub, wanted)
    await _fill_missing_indexes(hub, wanted, memory)
    records = await _load_records(hub, wanted, memory)
    messages = await _latest_messages(hub, records, memory.event_tails)
    slices = await _build_slices(hub, wanted, memory, records, messages)
    return HubInboxSnapshot(slices=slices, memory_scans=memory.scans)


class _MemoryState:
    __slots__ = (
        "grouped",
        "current",
        "pending",
        "failed",
        "completed",
        "event_tails",
        "scans",
    )

    def __init__(self, wanted: list[int]) -> None:
        self.grouped = {server_id: [] for server_id in wanted}
        self.current: dict[int, str | None] = {}
        self.pending: dict[int, list[str] | None] = {}
        self.failed: dict[int, list[str] | None] = {}
        self.completed: dict[int, list[str] | None] = {}
        self.event_tails: dict[str, dict[str, Any]] = {}
        self.scans = 0


async def _scan_memory(hub: InboxHub, wanted: list[int]) -> _MemoryState:
    wanted_set = set(wanted)
    state = _MemoryState(wanted)
    async with hub._lock:
        state.scans = 1
        for record in hub._records.values():
            server_id = int(record.get("server_id") or 0)
            if server_id in wanted_set:
                state.grouped[server_id].append(dict(record))
        for server_id in wanted:
            state.current[server_id] = (
                hub._current[server_id] if server_id in hub._current else None
            )
            state.pending[server_id] = (
                list(hub._pending[server_id]) if server_id in hub._pending else None
            )
            state.failed[server_id] = (
                list(hub._failed[server_id]) if server_id in hub._failed else None
            )
            state.completed[server_id] = (
                list(hub._completed[server_id]) if server_id in hub._completed else None
            )
        for operation_id, events in hub._events.items():
            if events:
                state.event_tails[operation_id] = events[-1]
    return state


async def _fill_missing_indexes(hub: InboxHub, wanted: list[int], state: _MemoryState) -> None:
    requests: list[tuple[str, int, str]] = []
    for server_id in wanted:
        if state.current[server_id] is None:
            requests.append(("current", server_id, hub._current_key(server_id)))
        if state.pending[server_id] is None:
            requests.append(("pending", server_id, hub._pending_key(server_id)))
        if state.failed[server_id] is None:
            requests.append(("failed", server_id, hub._failed_key(server_id)))
        if state.completed[server_id] is None:
            requests.append(("completed", server_id, hub._completed_key(server_id)))
    values = await get_many_chunked(hub._history_redis, [item[2] for item in requests])
    async with hub._lock:
        for (kind, server_id, _key), stored in zip(requests, values, strict=True):
            _apply_index(hub, state, kind, server_id, stored)


def _apply_index(
    hub: InboxHub,
    state: _MemoryState,
    kind: str,
    server_id: int,
    stored: object,
) -> None:
    if kind == "current":
        operation_id = current_id_from_stored(stored)
        state.current[server_id] = operation_id
        if operation_id and server_id not in hub._current:
            hub._current[server_id] = operation_id
        return
    ids = ids_from_stored(stored)
    if kind == "pending":
        state.pending[server_id] = ids
        hub._pending.setdefault(server_id, list(ids))
        return
    if kind == "failed":
        state.failed[server_id] = ids
        hub._failed.setdefault(server_id, list(ids))
        return
    state.completed[server_id] = ids
    hub._completed.setdefault(server_id, list(ids))


async def _load_records(
    hub: InboxHub,
    wanted: list[int],
    state: _MemoryState,
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for extras in state.grouped.values():
        for record in extras:
            operation_id = str(record.get("operation_id") or "")
            if operation_id:
                records[operation_id] = record
    needed: list[str] = []
    for server_id in wanted:
        for operation_id in _index_ids(state, server_id):
            if operation_id and operation_id not in records:
                needed.append(operation_id)
    unique = list(dict.fromkeys(needed))
    keys = [hub._record_key(operation_id) for operation_id in unique]
    stored_records = await get_many_chunked(hub._history_redis, keys)
    async with hub._lock:
        for operation_id, stored in zip(unique, stored_records, strict=True):
            if not isinstance(stored, dict):
                continue
            records[operation_id] = dict(stored)
            hub._records.setdefault(operation_id, stored)
    return records


def _index_ids(state: _MemoryState, server_id: int) -> list[str]:
    ids: list[str] = []
    current = state.current.get(server_id)
    if current:
        ids.append(current)
    ids.extend(state.pending.get(server_id) or [])
    ids.extend(state.failed.get(server_id) or [])
    ids.extend(state.completed.get(server_id) or [])
    return ids


async def _latest_messages(
    hub: InboxHub,
    records: dict[str, dict[str, Any]],
    event_tails: dict[str, dict[str, Any]],
) -> dict[str, str | None]:
    messages: dict[str, str | None] = {}
    need_redis: dict[str, str] = {}
    for operation_id in records:
        text = last_event_text([event_tails[operation_id]] if operation_id in event_tails else None)
        if text:
            messages[operation_id] = text
            continue
        need_redis[operation_id] = hub._events_key(operation_id)
    stored = await read_latest_messages(hub._history_redis, need_redis)
    for operation_id in records:
        if operation_id in messages:
            continue
        messages[operation_id] = stored.get(operation_id) or record_message(
            records.get(operation_id)
        )
    return messages


def _assemble_active(
    current_id: str | None,
    pending_ids: list[str],
    extras: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    if current_id:
        record = records.get(current_id)
        if record:
            items.append(dict(record))
            seen.add(current_id)
    for operation_id in pending_ids:
        if operation_id in seen:
            continue
        record = records.get(operation_id)
        if record:
            items.append(dict(record))
            seen.add(operation_id)
    for record in extras:
        operation_id = str(record.get("operation_id") or "")
        if not operation_id or operation_id in seen:
            continue
        items.append(record)
        seen.add(operation_id)
    return items


def _active_rows(
    assembled: list[dict[str, Any]],
    messages: dict[str, str | None],
) -> list[InboxRecordRow]:
    rows: list[InboxRecordRow] = []
    for position, record in enumerate(assembled):
        if record.get("status") not in ACTIVE_STATUSES:
            continue
        operation_id = str(record["operation_id"])
        rows.append(
            InboxRecordRow(
                record=record,
                latest_message=messages.get(operation_id),
                queue_position=position if record.get("status") == "queued" else 0,
            )
        )
    return rows


def _retained_rows(
    operation_ids: list[str],
    records: dict[str, dict[str, Any]],
    messages: dict[str, str | None],
    *,
    status: str,
    cutoff: datetime,
) -> tuple[list[InboxRecordRow], set[str], list[str]]:
    kept: list[str] = []
    items: list[dict[str, Any]] = []
    for operation_id in operation_ids:
        record = records.get(operation_id)
        if record is None or record.get("status") != status:
            continue
        completed = _as_datetime(record.get("completed_at"))
        if completed is not None and completed < cutoff:
            continue
        kept.append(operation_id)
        items.append(record)
    items.sort(
        key=lambda item: str(item.get("completed_at") or item.get("started_at") or ""),
        reverse=True,
    )
    rows = [
        InboxRecordRow(
            record=item,
            latest_message=messages.get(str(item["operation_id"])),
            queue_position=0,
        )
        for item in items
    ]
    expired = set(operation_ids) - set(kept)
    return rows, expired, operation_ids


async def _build_slices(
    hub: InboxHub,
    wanted: list[int],
    state: _MemoryState,
    records: dict[str, dict[str, Any]],
    messages: dict[str, str | None],
) -> dict[int, ServerInboxSlice]:
    now = datetime.now(timezone.utc)
    failed_cutoff = now - timedelta(seconds=FAILED_RETENTION_SECONDS)
    completed_cutoff = now - timedelta(seconds=COMPLETED_RETENTION_SECONDS)
    slices: dict[int, ServerInboxSlice] = {}
    for server_id in wanted:
        assembled = _assemble_active(
            state.current.get(server_id),
            list(state.pending.get(server_id) or []),
            state.grouped.get(server_id) or [],
            records,
        )
        failed_rows, failed_expired, failed_fallback = _retained_rows(
            list(state.failed.get(server_id) or []),
            records,
            messages,
            status="failed",
            cutoff=failed_cutoff,
        )
        completed_rows, completed_expired, completed_fallback = _retained_rows(
            list(state.completed.get(server_id) or []),
            records,
            messages,
            status="completed",
            cutoff=completed_cutoff,
        )
        await reconcile_retained_index(
            redis=hub._history_redis,
            lock=hub._lock,
            server_id=server_id,
            cache=hub._failed,
            redis_key=hub._failed_key(server_id),
            persister=hub._persist_failed,
            expired=failed_expired,
            fallback=failed_fallback,
        )
        await reconcile_retained_index(
            redis=hub._history_redis,
            lock=hub._lock,
            server_id=server_id,
            cache=hub._completed,
            redis_key=hub._completed_key(server_id),
            persister=hub._persist_completed,
            expired=completed_expired,
            fallback=completed_fallback,
        )
        slices[server_id] = ServerInboxSlice(
            active=_active_rows(assembled, messages),
            failed=failed_rows,
            completed=completed_rows,
        )
    return slices
