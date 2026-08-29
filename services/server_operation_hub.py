"""In-process + Redis registry for async server operations and SSE replay."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from typing import Any

from modules.utils import get_current_time
from services.ai_security import redact_sensitive_text
from services.redis_manager import redis_manager

logger = logging.getLogger(__name__)

OPERATION_TTL_SECONDS = 24 * 60 * 60
EVENT_LIMIT = 5000
SUBSCRIBER_QUEUE_LIMIT = 256
ACTIVE_STATUSES = frozenset({"queued", "running"})
TERMINAL_EVENT_TYPES = frozenset({"operation_completed", "operation_failed"})


class ServerOperationConflict(Exception):
    """Raised when a server already has a queued or running operation."""

    def __init__(self, message: str, operation_id: str | None = None) -> None:
        super().__init__(message)
        self.operation_id = operation_id


class ServerOperationHub:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._current: dict[int, str] = {}
        self._events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._queues: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def _record_key(self, operation_id: str) -> str:
        return f"server_op:{operation_id}"

    def _current_key(self, server_id: int) -> str:
        return f"server_op_current:{server_id}"

    def _events_key(self, operation_id: str) -> str:
        return f"server_op:{operation_id}:events"

    async def create(
        self,
        *,
        server_id: int,
        action: str,
        actor_user_id: int,
    ) -> dict[str, Any]:
        async with self._lock:
            current = await self._read_current(server_id)
            if current and current.get("status") in ACTIVE_STATUSES:
                raise ServerOperationConflict(
                    "An operation is already running on this server",
                    operation_id=str(current.get("operation_id") or "") or None,
                )
            operation_id = str(uuid.uuid4())
            record = {
                "operation_id": operation_id,
                "server_id": server_id,
                "action": action,
                "status": "queued",
                "success": None,
                "message": None,
                "server_status": None,
                "actor_user_id": actor_user_id,
                "started_at": get_current_time().isoformat(),
                "completed_at": None,
            }
            self._records[operation_id] = record
            self._current[server_id] = operation_id
            self._events[operation_id] = []
        await self._persist_record(record)
        await redis_manager.set(
            self._current_key(server_id), operation_id, expire=OPERATION_TTL_SECONDS
        )
        await self.emit(
            operation_id,
            "progress",
            kind="status",
            message=f"Operation accepted: {action} (queued)",
        )
        return dict(record)

    async def get(self, operation_id: str) -> dict[str, Any] | None:
        record = self._records.get(operation_id)
        if record is not None:
            return dict(record)
        stored = await redis_manager.get(self._record_key(operation_id))
        if not isinstance(stored, dict):
            return None
        self._records[operation_id] = stored
        return dict(stored)

    async def get_current(self, server_id: int) -> dict[str, Any] | None:
        async with self._lock:
            return await self._read_current(server_id)

    async def mark_running(self, operation_id: str) -> dict[str, Any] | None:
        record = await self._update(operation_id, status="running")
        if record is None:
            return None
        action = str(record.get("action") or "operation")
        await self.emit(
            operation_id,
            "progress",
            kind="status",
            message=f"Worker started {action}",
        )
        return record

    def bind_task(self, operation_id: str, task: asyncio.Task) -> None:
        """Remember the process-local task so force-stop can cancel it."""
        self._tasks[operation_id] = task

    async def abort(self, server_id: int, *, message: str) -> dict[str, Any] | None:
        """Cancel the current operation and mark it failed."""
        record = await self.get_current(server_id)
        if record is None or record.get("status") not in ACTIVE_STATUSES:
            return None
        operation_id = str(record["operation_id"])
        task = self._tasks.pop(operation_id, None)
        if task is not None and not task.done():
            task.cancel()
        return await self.finish(operation_id, success=False, message=message)

    async def finish(
        self,
        operation_id: str,
        *,
        success: bool,
        message: str,
        server_status: str | None = None,
    ) -> dict[str, Any] | None:
        current = await self.get(operation_id)
        if current is not None and current.get("status") not in ACTIVE_STATUSES:
            return current
        self._tasks.pop(operation_id, None)
        record = await self._update(
            operation_id,
            status="completed" if success else "failed",
            success=success,
            message=redact_sensitive_text(message, limit=2000),
            server_status=server_status,
            completed_at=get_current_time().isoformat(),
        )
        event_type = "operation_completed" if success else "operation_failed"
        await self.emit(
            operation_id,
            event_type,
            kind="complete" if success else "error",
            message=message,
            extra={"success": success, "server_status": server_status},
        )
        return record

    async def emit(
        self,
        operation_id: str,
        event_type: str,
        *,
        kind: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        timestamp = get_current_time().isoformat()
        event = {
            "sequence": str(time.time_ns()),
            "operation_id": operation_id,
            "type": event_type,
            "kind": kind,
            "message": redact_sensitive_text(message, limit=64 * 1024),
            "timestamp": timestamp,
            **(extra or {}),
        }
        async with self._lock:
            self._events[operation_id].append(event)
            if len(self._events[operation_id]) > EVENT_LIMIT:
                self._events[operation_id] = self._events[operation_id][-EVENT_LIMIT:]
            queues = list(self._queues.get(operation_id, set()))
        await self._persist_event(operation_id, event)
        for queue in queues:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("SSE subscriber for operation %s is not keeping up", operation_id)
        return event

    async def forward_progress(
        self,
        server_id: int,
        msg_type: str,
        message: str,
        timestamp: str,
    ) -> None:
        current = await self.get_current(server_id)
        if current is None or current.get("status") not in ACTIVE_STATUSES:
            return
        await self.emit(
            str(current["operation_id"]),
            "progress",
            kind=msg_type,
            message=message,
            extra={"timestamp": timestamp},
        )

    async def replay(self, operation_id: str, after_sequence: int = 0) -> list[dict[str, Any]]:
        events = list(self._events.get(operation_id) or [])
        if not events:
            events = await self._load_events(operation_id)
            if events:
                async with self._lock:
                    if not self._events.get(operation_id):
                        self._events[operation_id] = events
        replayed: list[dict[str, Any]] = []
        for event in events:
            try:
                sequence = int(event.get("sequence") or 0)
            except TypeError, ValueError:
                continue
            if sequence > after_sequence:
                replayed.append(event)
        return replayed

    async def subscribe_queue(self, operation_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_LIMIT)
        async with self._lock:
            self._queues[operation_id].add(queue)
        return queue

    async def unsubscribe_queue(self, operation_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            queues = self._queues.get(operation_id)
            if queues is None:
                return
            queues.discard(queue)
            if not queues:
                self._queues.pop(operation_id, None)

    async def _read_current(self, server_id: int) -> dict[str, Any] | None:
        operation_id = self._current.get(server_id)
        if operation_id is None:
            stored = await redis_manager.get(self._current_key(server_id))
            if isinstance(stored, str) and stored:
                operation_id = stored
                self._current[server_id] = stored
        if not operation_id:
            return None
        record = self._records.get(operation_id)
        if record is None:
            stored_record = await redis_manager.get(self._record_key(operation_id))
            if isinstance(stored_record, dict):
                self._records[operation_id] = stored_record
                record = stored_record
        return dict(record) if record else None

    async def _update(self, operation_id: str, **changes: Any) -> dict[str, Any] | None:
        async with self._lock:
            record = self._records.get(operation_id)
            if record is None:
                stored = await redis_manager.get(self._record_key(operation_id))
                if not isinstance(stored, dict):
                    return None
                record = stored
                self._records[operation_id] = record
            record.update(changes)
            snapshot = dict(record)
        await self._persist_record(snapshot)
        return snapshot

    async def _persist_record(self, record: dict[str, Any]) -> None:
        try:
            await redis_manager.set(
                self._record_key(str(record["operation_id"])),
                record,
                expire=OPERATION_TTL_SECONDS,
            )
        except Exception as exc:
            logger.warning(
                "Unable to persist server operation %s: %s", record.get("operation_id"), exc
            )

    async def _persist_event(self, operation_id: str, event: dict[str, Any]) -> None:
        key = self._events_key(operation_id)
        try:
            encoded = json.dumps(event, ensure_ascii=False, default=str)
            pipeline = redis_manager.client.pipeline(transaction=False)
            pipeline.rpush(key, encoded)
            pipeline.ltrim(key, -EVENT_LIMIT, -1)
            pipeline.expire(key, OPERATION_TTL_SECONDS)
            await pipeline.execute()
        except Exception as exc:
            logger.warning("Unable to persist operation event %s: %s", operation_id, exc)

    async def _load_events(self, operation_id: str) -> list[dict[str, Any]]:
        try:
            values = await redis_manager.client.lrange(self._events_key(operation_id), 0, -1)
        except Exception as exc:
            logger.warning("Unable to load operation events %s: %s", operation_id, exc)
            return []
        events: list[dict[str, Any]] = []
        for value in values:
            try:
                events.append(json.loads(value))
            except TypeError, json.JSONDecodeError:
                continue
        return events


server_operation_hub = ServerOperationHub()
