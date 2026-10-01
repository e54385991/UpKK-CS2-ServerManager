"""Focused helpers; mutable state remains owned by the original controller."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .server_operation_dependencies import OperationDependencies

if TYPE_CHECKING:
    from .server_operation_hub import ServerOperationHub


async def _expire_events(
    self: ServerOperationHub, dependencies: OperationDependencies, operation_id: str, expire: int
) -> None:
    try:
        await dependencies.redis_manager.client.expire(
            dependencies.redis_manager.prefixed_key(self._events_key(operation_id)), expire
        )
    except Exception as exc:
        dependencies.logger.warning("Unable to refresh event TTL for %s: %s", operation_id, exc)


async def _persist_pending(
    self: ServerOperationHub, dependencies: OperationDependencies, server_id: int
) -> None:
    try:
        await dependencies.redis_manager.set(
            self._pending_key(server_id),
            list(self._pending.get(server_id) or []),
            expire=dependencies.operation_ttl,
        )
    except Exception as exc:
        dependencies.logger.warning(
            "Unable to persist pending operations for server %s: %s", server_id, exc
        )


async def _read_current(
    self: ServerOperationHub, dependencies: OperationDependencies, server_id: int
) -> dict[str, Any] | None:
    operation_id = self._current.get(server_id)
    if operation_id is None:
        stored = await dependencies.redis_manager.get(self._current_key(server_id))
        if isinstance(stored, str) and stored:
            operation_id = stored
            self._current[server_id] = stored
    if not operation_id:
        return None
    record = self._records.get(operation_id)
    if record is None:
        stored_record = await dependencies.redis_manager.get(self._record_key(operation_id))
        if isinstance(stored_record, dict):
            self._records[operation_id] = stored_record
            record = stored_record
    return dict(record) if record else None


async def _update(
    self: ServerOperationHub, dependencies: OperationDependencies, operation_id: str, **changes: Any
) -> dict[str, Any] | None:
    async with self._lock:
        record = self._records.get(operation_id)
        if record is None:
            stored = await dependencies.redis_manager.get(self._record_key(operation_id))
            if not isinstance(stored, dict):
                return None
            record = stored
            self._records[operation_id] = record
        record.update(changes)
        snapshot = dict(record)
    await self._persist_record(snapshot)
    return snapshot


async def _finish_record(
    self: ServerOperationHub, dependencies: OperationDependencies, operation_id: str, **changes: Any
) -> tuple[dict[str, Any] | None, bool]:
    """Apply the terminal transition once and report whether this call applied it.

    The status check and the update share one lock hold, so only one of
    several concurrent finishers emits the terminal event and promotes the
    next job. An unknown operation keeps the previous best-effort path.
    """
    async with self._lock:
        record = self._records.get(operation_id)
        if record is None:
            stored = await dependencies.redis_manager.get(self._record_key(operation_id))
            if not isinstance(stored, dict):
                return None, True
            record = stored
            self._records[operation_id] = record
        if record.get("status") not in dependencies.active_statuses:
            return dict(record), False
        record.update(changes)
        snapshot = dict(record)
    await self._persist_record(snapshot)
    return snapshot, True


async def _persist_record(
    self: ServerOperationHub, dependencies: OperationDependencies, record: dict[str, Any]
) -> None:
    try:
        await dependencies.redis_manager.set(
            self._record_key(str(record["operation_id"])),
            record,
            expire=dependencies.record_ttl(record),
        )
    except Exception as exc:
        dependencies.logger.warning(
            "Unable to persist server operation %s: %s", record.get("operation_id"), exc
        )


async def _persist_event(
    self: ServerOperationHub,
    dependencies: OperationDependencies,
    operation_id: str,
    event: dict[str, Any],
) -> None:
    key = dependencies.redis_manager.prefixed_key(self._events_key(operation_id))
    try:
        encoded = json.dumps(event, ensure_ascii=False, default=str)
        pipeline = dependencies.redis_manager.client.pipeline(transaction=False)
        pipeline.rpush(key, encoded)
        pipeline.ltrim(key, -dependencies.event_limit, -1)
        pipeline.expire(key, dependencies.operation_ttl)
        await pipeline.execute()
    except Exception as exc:
        dependencies.logger.warning("Unable to persist operation event %s: %s", operation_id, exc)


async def _load_events(
    self: ServerOperationHub, dependencies: OperationDependencies, operation_id: str
) -> list[dict[str, Any]]:
    try:
        values = await dependencies.redis_manager.client.lrange(
            dependencies.redis_manager.prefixed_key(self._events_key(operation_id)), 0, -1
        )
    except Exception as exc:
        dependencies.logger.warning("Unable to load operation events %s: %s", operation_id, exc)
        return []
    events: list[dict[str, Any]] = []
    for value in values:
        try:
            events.append(json.loads(value))
        except TypeError, json.JSONDecodeError:
            continue
    return dependencies.trim_events(events)


async def _pending_ids_unlocked(
    self: ServerOperationHub, dependencies: OperationDependencies, server_id: int
) -> list[str]:
    cached = self._pending.get(server_id)
    if cached is not None:
        return list(cached)
    stored = await dependencies.redis_manager.get(self._pending_key(server_id))
    ids: list[str] = []
    if isinstance(stored, list):
        ids = [str(item) for item in stored if item]
    elif isinstance(stored, str) and stored:
        try:
            parsed = json.loads(stored)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            ids = [str(item) for item in parsed if item]
    self._pending[server_id] = ids
    return list(ids)
