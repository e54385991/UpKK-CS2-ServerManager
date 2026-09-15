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
    memory, records = await _scan_memory(hub, wanted)
    await _fill_missing_indexes(hub, wanted, memory)
    records = await _load_records(hub, wanted, memory, records)
    plans = _plan_slices(wanted, memory, records)
    visible = _message_ids(plans)
    tails = await _memory_event_tails(hub, visible)
    messages = await _latest_messages(hub, visible, records, tails)
    slices = await _apply_plans(hub, plans, messages)
    return HubInboxSnapshot(slices=slices, memory_scans=memory.scans)


class _MemoryState:
    __slots__ = (
        "grouped",
        "current",
        "pending",
        "failed",
        "completed",
        "scans",
    )

    def __init__(self, wanted: list[int]) -> None:
        self.grouped = {server_id: [] for server_id in wanted}
        self.current: dict[int, str | None] = {}
        self.pending: dict[int, list[str] | None] = {}
        self.failed: dict[int, list[str] | None] = {}
        self.completed: dict[int, list[str] | None] = {}
        self.scans = 0


async def _scan_memory(
    hub: InboxHub, wanted: list[int]
) -> tuple[_MemoryState, dict[str, dict[str, Any]]]:
    wanted_set = set(wanted)
    state = _MemoryState(wanted)
    records: dict[str, dict[str, Any]] = {}
    async with hub._lock:
        state.scans = 1
        for record in hub._records.values():
            server_id = int(record.get("server_id") or 0)
            if server_id not in wanted_set:
                continue
            operation_id = str(record.get("operation_id") or "")
            if not operation_id:
                continue
            records[operation_id] = dict(record)
            state.grouped[server_id].append(operation_id)
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
    return state, records


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
    records: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
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


async def _memory_event_tails(
    hub: InboxHub, operation_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    tails: dict[str, dict[str, Any]] = {}
    async with hub._lock:
        for operation_id in operation_ids:
            events = hub._events.get(operation_id)
            if events:
                tails[operation_id] = events[-1]
    return tails


async def _latest_messages(
    hub: InboxHub,
    operation_ids: Sequence[str],
    records: dict[str, dict[str, Any]],
    event_tails: dict[str, dict[str, Any]],
) -> dict[str, str | None]:
    messages: dict[str, str | None] = {}
    need_redis: dict[str, str] = {}
    for operation_id in operation_ids:
        text = last_event_text([event_tails[operation_id]] if operation_id in event_tails else None)
        if text:
            messages[operation_id] = text
            continue
        need_redis[operation_id] = hub._events_key(operation_id)
    stored = await read_latest_messages(hub._history_redis, need_redis)
    for operation_id in operation_ids:
        if operation_id in messages:
            continue
        messages[operation_id] = stored.get(operation_id) or record_message(
            records.get(operation_id)
        )
    return messages


def _assemble_active(
    current_id: str | None,
    pending_ids: list[str],
    extras: list[str],
    records: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for operation_id in [current_id or "", *pending_ids, *extras]:
        if not operation_id or operation_id in seen:
            continue
        record = records.get(operation_id)
        if record is None:
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


class _ServerPlan:
    __slots__ = (
        "server_id",
        "assembled",
        "failed_items",
        "completed_items",
        "failed_expired",
        "completed_expired",
        "failed_fallback",
        "completed_fallback",
    )

    def __init__(
        self,
        server_id: int,
        assembled: list[dict[str, Any]],
        failed_items: list[dict[str, Any]],
        completed_items: list[dict[str, Any]],
        failed_expired: set[str],
        completed_expired: set[str],
        failed_fallback: list[str],
        completed_fallback: list[str],
    ) -> None:
        self.server_id = server_id
        self.assembled = assembled
        self.failed_items = failed_items
        self.completed_items = completed_items
        self.failed_expired = failed_expired
        self.completed_expired = completed_expired
        self.failed_fallback = failed_fallback
        self.completed_fallback = completed_fallback


def _partition_retained(
    operation_ids: list[str],
    records: dict[str, dict[str, Any]],
    *,
    status: str,
    cutoff: datetime,
) -> tuple[list[dict[str, Any]], set[str], list[str]]:
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
    return items, set(operation_ids) - set(kept), operation_ids


def _plan_slices(
    wanted: list[int],
    state: _MemoryState,
    records: dict[str, dict[str, Any]],
) -> list[_ServerPlan]:
    now = datetime.now(timezone.utc)
    failed_cutoff = now - timedelta(seconds=FAILED_RETENTION_SECONDS)
    completed_cutoff = now - timedelta(seconds=COMPLETED_RETENTION_SECONDS)
    plans: list[_ServerPlan] = []
    for server_id in wanted:
        assembled = _assemble_active(
            state.current.get(server_id),
            list(state.pending.get(server_id) or []),
            list(state.grouped.get(server_id) or []),
            records,
        )
        failed_items, failed_expired, failed_fallback = _partition_retained(
            list(state.failed.get(server_id) or []),
            records,
            status="failed",
            cutoff=failed_cutoff,
        )
        completed_items, completed_expired, completed_fallback = _partition_retained(
            list(state.completed.get(server_id) or []),
            records,
            status="completed",
            cutoff=completed_cutoff,
        )
        plans.append(
            _ServerPlan(
                server_id,
                assembled,
                failed_items,
                completed_items,
                failed_expired,
                completed_expired,
                failed_fallback,
                completed_fallback,
            )
        )
    return plans


def _message_ids(plans: list[_ServerPlan]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for plan in plans:
        for record in (
            *[item for item in plan.assembled if item.get("status") in ACTIVE_STATUSES],
            *plan.failed_items,
            *plan.completed_items,
        ):
            operation_id = str(record.get("operation_id") or "")
            if not operation_id or operation_id in seen:
                continue
            seen.add(operation_id)
            ids.append(operation_id)
    return ids


def _rows_for(
    items: list[dict[str, Any]],
    messages: dict[str, str | None],
    *,
    queued_positions: bool,
) -> list[InboxRecordRow]:
    rows: list[InboxRecordRow] = []
    for position, record in enumerate(items):
        operation_id = str(record["operation_id"])
        rows.append(
            InboxRecordRow(
                record=record,
                latest_message=messages.get(operation_id),
                queue_position=(
                    position if queued_positions and record.get("status") == "queued" else 0
                ),
            )
        )
    return rows


async def _apply_plans(
    hub: InboxHub,
    plans: list[_ServerPlan],
    messages: dict[str, str | None],
) -> dict[int, ServerInboxSlice]:
    slices: dict[int, ServerInboxSlice] = {}
    for plan in plans:
        await reconcile_retained_index(
            redis=hub._history_redis,
            lock=hub._lock,
            server_id=plan.server_id,
            cache=hub._failed,
            redis_key=hub._failed_key(plan.server_id),
            persister=hub._persist_failed,
            expired=plan.failed_expired,
            fallback=plan.failed_fallback,
        )
        await reconcile_retained_index(
            redis=hub._history_redis,
            lock=hub._lock,
            server_id=plan.server_id,
            cache=hub._completed,
            redis_key=hub._completed_key(plan.server_id),
            persister=hub._persist_completed,
            expired=plan.completed_expired,
            fallback=plan.completed_fallback,
        )
        slices[plan.server_id] = ServerInboxSlice(
            active=_active_rows(plan.assembled, messages),
            failed=_rows_for(plan.failed_items, messages, queued_positions=False),
            completed=_rows_for(plan.completed_items, messages, queued_positions=False),
        )
    return slices
