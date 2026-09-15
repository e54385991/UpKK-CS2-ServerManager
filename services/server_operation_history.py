"""Retention indexes and history cleanup for server operations."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

logger = logging.getLogger(__name__)

FAILED_RETENTION_SECONDS = 7 * 24 * 60 * 60
COMPLETED_RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_FAILED_PER_SERVER = 100
MAX_COMPLETED_PER_SERVER = 100

RetainedIdLoader = Callable[[int], Awaitable[list[str]]]
RetainedPersister = Callable[[int], Awaitable[None]]


def ids_from_stored(stored: object) -> list[str]:
    """Decode a retained or pending index from Redis JSON or a native list."""
    if isinstance(stored, list):
        return [str(item) for item in stored if item]
    if isinstance(stored, str) and stored:
        try:
            parsed = json.loads(stored)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item]
    return []


def current_id_from_stored(stored: object) -> str | None:
    """Decode the current-operation pointer stored as a raw string."""
    if isinstance(stored, str) and stored:
        return stored
    return None


def merge_retained_ids(persisted: list[str], live: list[str], fallback: list[str]) -> list[str]:
    """Prefer persisted order, then live adds; fallback only when both are empty."""
    if not persisted and not live:
        return list(dict.fromkeys(item for item in fallback if item))
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*persisted, *live]:
        if item and item not in seen:
            seen.add(item)
            merged.append(item)
    return merged


async def reconcile_retained_index(
    *,
    redis: Any,
    lock: Any,
    server_id: int,
    cache: dict[int, list[str]],
    redis_key: str,
    persister: RetainedPersister,
    expired: set[str],
    fallback: list[str],
) -> None:
    """Drop known-expired IDs without wiping IDs added during the read."""
    if not expired:
        return
    try:
        stored = await redis.get(redis_key)
    except Exception:
        stored = None
    persist = stored is not None
    async with lock:
        live = list(cache.get(server_id) or [])
        persisted = ids_from_stored(stored) if stored is not None else []
        merged = merge_retained_ids(persisted, live, fallback)
        cache[server_id] = [item for item in merged if item not in expired]
    if persist:
        await persister(server_id)


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str) and value:
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


class HistoryHost(Protocol):
    _lock: Any
    _current: dict[int, str]
    _failed: dict[int, list[str]]
    _completed: dict[int, list[str]]

    @property
    def _history_redis(self) -> Any: ...

    async def get(self, operation_id: str) -> dict[str, Any] | None: ...

    def _current_key(self, server_id: int) -> str: ...

    async def _forget_operation(self, operation_id: str) -> None: ...

    async def _persist_record(self, record: dict[str, Any]) -> None: ...

    async def _expire_events(self, operation_id: str, expire: int) -> None: ...

    def _failed_key(self, server_id: int) -> str: ...

    def _completed_key(self, server_id: int) -> str: ...

    async def _retained_ids_unlocked(
        self, server_id: int, cache: dict[int, list[str]], key: str
    ) -> list[str]: ...

    async def _failed_ids_unlocked(self, server_id: int) -> list[str]: ...

    async def _completed_ids_unlocked(self, server_id: int) -> list[str]: ...

    async def _persist_retained(
        self,
        server_id: int,
        cache: dict[int, list[str]],
        key: str,
        expire: int,
        label: str,
    ) -> None: ...

    async def _persist_failed(self, server_id: int) -> None: ...

    async def _persist_completed(self, server_id: int) -> None: ...

    async def _list_retained_for_server(
        self,
        server_id: int,
        *,
        status: str,
        retention: int,
        ids_cache: dict[int, list[str]],
        ids_loader: RetainedIdLoader,
        ids_persister: RetainedPersister,
    ) -> list[dict[str, Any]]: ...

    async def list_failed_for_server(self, server_id: int) -> list[dict[str, Any]]: ...

    async def list_completed_for_server(self, server_id: int) -> list[dict[str, Any]]: ...

    async def _dismiss_retained(
        self,
        operation_id: str,
        *,
        status: str,
        ids_cache: dict[int, list[str]],
        ids_loader: RetainedIdLoader,
        ids_persister: RetainedPersister,
    ) -> dict[str, Any] | None: ...

    async def dismiss_failed(self, operation_id: str) -> dict[str, Any] | None: ...

    async def dismiss_completed(self, operation_id: str) -> dict[str, Any] | None: ...

    async def _clear_retained(
        self,
        server_ids: list[int],
        *,
        list_retained: Callable[[int], Awaitable[list[dict[str, Any]]]],
        dismiss_retained: Callable[[str], Awaitable[dict[str, Any] | None]],
    ) -> int: ...

    async def clear_failed(self, server_ids: list[int]) -> int: ...

    async def clear_completed(self, server_ids: list[int]) -> int: ...

    async def _remember_retained(
        self,
        record: dict[str, Any],
        *,
        cache: dict[int, list[str]],
        ids_loader: RetainedIdLoader,
        ids_persister: RetainedPersister,
        max_items: int,
        event_ttl: int,
    ) -> None: ...

    async def remember_failed(self, record: dict[str, Any]) -> None: ...

    async def remember_completed(self, record: dict[str, Any]) -> None: ...


class ServerOperationHistoryMixin:
    """Provide bounded Redis-backed indexes for terminal operations."""

    def _failed_key(self: HistoryHost, server_id: int) -> str:
        return f"server_op_failed:{server_id}"

    def _completed_key(self: HistoryHost, server_id: int) -> str:
        return f"server_op_completed:{server_id}"

    async def _retained_ids_unlocked(
        self: HistoryHost,
        server_id: int,
        cache: dict[int, list[str]],
        key: str,
    ) -> list[str]:
        cached = cache.get(server_id)
        if cached is not None:
            return list(cached)
        stored = await self._history_redis.get(key)
        ids = ids_from_stored(stored)
        cache[server_id] = ids
        return list(ids)

    async def _failed_ids_unlocked(self: HistoryHost, server_id: int) -> list[str]:
        return await self._retained_ids_unlocked(
            server_id, self._failed, self._failed_key(server_id)
        )

    async def _completed_ids_unlocked(self: HistoryHost, server_id: int) -> list[str]:
        return await self._retained_ids_unlocked(
            server_id, self._completed, self._completed_key(server_id)
        )

    async def _persist_retained(
        self: HistoryHost,
        server_id: int,
        cache: dict[int, list[str]],
        key: str,
        expire: int,
        label: str,
    ) -> None:
        try:
            await self._history_redis.set(key, list(cache.get(server_id) or []), expire=expire)
        except Exception as exc:
            logger.warning(
                "Unable to persist %s operations for server %s: %s", label, server_id, exc
            )

    async def _persist_failed(self: HistoryHost, server_id: int) -> None:
        await self._persist_retained(
            server_id,
            self._failed,
            self._failed_key(server_id),
            FAILED_RETENTION_SECONDS,
            "failed",
        )

    async def _persist_completed(self: HistoryHost, server_id: int) -> None:
        await self._persist_retained(
            server_id,
            self._completed,
            self._completed_key(server_id),
            COMPLETED_RETENTION_SECONDS,
            "completed",
        )

    async def _list_retained_for_server(
        self: HistoryHost,
        server_id: int,
        *,
        status: str,
        retention: int,
        ids_cache: dict[int, list[str]],
        ids_loader: RetainedIdLoader,
        ids_persister: RetainedPersister,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            operation_ids = await ids_loader(server_id)
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=retention)
        kept: list[str] = []
        items: list[dict[str, Any]] = []
        for operation_id in operation_ids:
            record = await self.get(operation_id)
            if record is None or record.get("status") != status:
                continue
            completed = _as_datetime(record.get("completed_at"))
            if completed is not None and completed < cutoff:
                continue
            kept.append(operation_id)
            items.append(record)
        if kept != operation_ids:
            redis_key = (
                self._failed_key(server_id)
                if status == "failed"
                else self._completed_key(server_id)
            )
            await reconcile_retained_index(
                redis=self._history_redis,
                lock=self._lock,
                server_id=server_id,
                cache=ids_cache,
                redis_key=redis_key,
                persister=ids_persister,
                expired=set(operation_ids) - set(kept),
                fallback=operation_ids,
            )
        items.sort(
            key=lambda item: str(item.get("completed_at") or item.get("started_at") or ""),
            reverse=True,
        )
        return items

    async def list_failed_for_server(self: HistoryHost, server_id: int) -> list[dict[str, Any]]:
        """Failed jobs still inside the seven-day retention window."""
        return await self._list_retained_for_server(
            server_id,
            status="failed",
            retention=FAILED_RETENTION_SECONDS,
            ids_cache=self._failed,
            ids_loader=self._failed_ids_unlocked,
            ids_persister=self._persist_failed,
        )

    async def list_completed_for_server(self: HistoryHost, server_id: int) -> list[dict[str, Any]]:
        """Successful jobs still inside the seven-day history window."""
        return await self._list_retained_for_server(
            server_id,
            status="completed",
            retention=COMPLETED_RETENTION_SECONDS,
            ids_cache=self._completed,
            ids_loader=self._completed_ids_unlocked,
            ids_persister=self._persist_completed,
        )

    async def _dismiss_retained(
        self: HistoryHost,
        operation_id: str,
        *,
        status: str,
        ids_cache: dict[int, list[str]],
        ids_loader: RetainedIdLoader,
        ids_persister: RetainedPersister,
    ) -> dict[str, Any] | None:
        record = await self.get(operation_id)
        if record is None or record.get("status") != status:
            return None
        server_id = int(record["server_id"])
        async with self._lock:
            operation_ids = await ids_loader(server_id)
            ids_cache[server_id] = [item for item in operation_ids if item != operation_id]
            was_current = self._current.get(server_id) == operation_id
            if was_current:
                self._current.pop(server_id, None)
        await ids_persister(server_id)
        if was_current:
            try:
                await self._history_redis.delete(self._current_key(server_id))
            except Exception as exc:
                logger.warning("Unable to clear current pointer for server %s: %s", server_id, exc)
        await self._forget_operation(operation_id)
        return record

    async def dismiss_failed(self: HistoryHost, operation_id: str) -> dict[str, Any] | None:
        """Drop one failed job from the inbox."""
        return await self._dismiss_retained(
            operation_id,
            status="failed",
            ids_cache=self._failed,
            ids_loader=self._failed_ids_unlocked,
            ids_persister=self._persist_failed,
        )

    async def dismiss_completed(self: HistoryHost, operation_id: str) -> dict[str, Any] | None:
        """Drop one completed job and its replayable history."""
        return await self._dismiss_retained(
            operation_id,
            status="completed",
            ids_cache=self._completed,
            ids_loader=self._completed_ids_unlocked,
            ids_persister=self._persist_completed,
        )

    async def _clear_retained(
        self: HistoryHost,
        server_ids: list[int],
        *,
        list_retained: Callable[[int], Awaitable[list[dict[str, Any]]]],
        dismiss_retained: Callable[[str], Awaitable[dict[str, Any] | None]],
    ) -> int:
        cleared = 0
        for server_id in server_ids:
            for record in await list_retained(server_id):
                if await dismiss_retained(str(record["operation_id"])):
                    cleared += 1
        return cleared

    async def clear_failed(self: HistoryHost, server_ids: list[int]) -> int:
        """Dismiss every retained failure for the given servers."""
        return await self._clear_retained(
            server_ids,
            list_retained=self.list_failed_for_server,
            dismiss_retained=self.dismiss_failed,
        )

    async def clear_completed(self: HistoryHost, server_ids: list[int]) -> int:
        """Dismiss every retained successful job for the given servers."""
        return await self._clear_retained(
            server_ids,
            list_retained=self.list_completed_for_server,
            dismiss_retained=self.dismiss_completed,
        )

    async def _remember_retained(
        self: HistoryHost,
        record: dict[str, Any],
        *,
        cache: dict[int, list[str]],
        ids_loader: RetainedIdLoader,
        ids_persister: RetainedPersister,
        max_items: int,
        event_ttl: int,
    ) -> None:
        server_id, operation_id = int(record["server_id"]), str(record["operation_id"])
        async with self._lock:
            operation_ids = await ids_loader(server_id)
            if operation_id not in operation_ids:
                operation_ids.append(operation_id)
            cache[server_id] = operation_ids[-max_items:]
        await ids_persister(server_id)
        await self._persist_record(record)
        await self._expire_events(operation_id, event_ttl)

    async def remember_failed(self: HistoryHost, record: dict[str, Any]) -> None:
        await self._remember_retained(
            record,
            cache=self._failed,
            ids_loader=self._failed_ids_unlocked,
            ids_persister=self._persist_failed,
            max_items=MAX_FAILED_PER_SERVER,
            event_ttl=FAILED_RETENTION_SECONDS,
        )

    async def remember_completed(self: HistoryHost, record: dict[str, Any]) -> None:
        await self._remember_retained(
            record,
            cache=self._completed,
            ids_loader=self._completed_ids_unlocked,
            ids_persister=self._persist_completed,
            max_items=MAX_COMPLETED_PER_SERVER,
            event_ttl=COMPLETED_RETENTION_SECONDS,
        )
