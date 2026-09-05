"""In-process + Redis registry for async server operations and SSE replay."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from modules.utils import get_current_time
from services.ai_security import redact_sensitive_text
from services.redis_manager import redis_manager

logger = logging.getLogger(__name__)

OPERATION_TTL_SECONDS = 24 * 60 * 60
FAILED_RETENTION_SECONDS = 7 * 24 * 60 * 60
EVENT_LIMIT = 300
SUBSCRIBER_QUEUE_LIMIT = 256
ACTIVE_STATUSES = frozenset({"queued", "running"})
TERMINAL_EVENT_TYPES = frozenset({"operation_completed", "operation_failed"})
MAX_PENDING_PER_SERVER = 10
MAX_FAILED_PER_SERVER = 100


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


def _trim_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(events) > EVENT_LIMIT:
        return events[-EVENT_LIMIT:]
    return events


def _record_ttl(record: dict[str, Any]) -> int:
    if record.get("status") == "failed":
        return FAILED_RETENTION_SECONDS
    return OPERATION_TTL_SECONDS


class ServerOperationConflict(Exception):
    """Raised when a server already has a queued or running operation."""

    def __init__(self, message: str, operation_id: str | None = None) -> None:
        super().__init__(message)
        self.operation_id = operation_id


class ServerOperationHub:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._current: dict[int, str] = {}
        self._pending: dict[int, list[str]] = {}
        self._failed: dict[int, list[str]] = {}
        self._runners: dict[str, Any] = {}
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

    def _pending_key(self, server_id: int) -> str:
        return f"server_op_pending:{server_id}"

    def _failed_key(self, server_id: int) -> str:
        return f"server_op_failed:{server_id}"

    async def create(
        self,
        *,
        server_id: int,
        action: str,
        actor_user_id: int,
        command: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            current = await self._read_current(server_id)
            pending = await self._pending_ids_unlocked(server_id)
            busy = bool(current and current.get("status") in ACTIVE_STATUSES)
            if busy and len(pending) >= MAX_PENDING_PER_SERVER:
                raise ServerOperationConflict(
                    "Too many queued operations on this server",
                    operation_id=(str(current.get("operation_id") or "") or None)
                    if current
                    else None,
                )
            operation_id = str(uuid.uuid4())
            record = {
                "operation_id": operation_id,
                "server_id": server_id,
                "action": action,
                "command": command,
                "status": "queued",
                "success": None,
                "message": None,
                "server_status": None,
                "actor_user_id": actor_user_id,
                "started_at": get_current_time().isoformat(),
                "completed_at": None,
            }
            if extra:
                for key in (
                    "destination",
                    "target_path",
                    "archive_path",
                    "clear_execstack",
                    "clear_execstack_command",
                    "clear_execstack_targets",
                ):
                    value = extra.get(key)
                    if value is not None:
                        record[key] = value
            self._records[operation_id] = record
            self._events[operation_id] = []
            queued_behind = busy
            if not busy:
                self._current[server_id] = operation_id
            else:
                pending.append(operation_id)
                self._pending[server_id] = pending
        await self._persist_record(record)
        if queued_behind:
            await self._persist_pending(server_id)
            position = len(pending)
            ahead = str((current or {}).get("action") or "operation")
            await self.emit(
                operation_id,
                "progress",
                kind="status",
                message=(
                    f"Queued behind {ahead} (position {position})"
                    + (f": {command}" if command else "")
                ),
            )
        else:
            await redis_manager.set(
                self._current_key(server_id), operation_id, expire=OPERATION_TTL_SECONDS
            )
            await self.emit(
                operation_id,
                "progress",
                kind="status",
                message=f"Operation accepted: {action} (queued)"
                + (f": {command}" if command else ""),
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

    async def schedule(self, operation_id: str, factory: Any) -> None:
        """Start the worker now if this job is current; otherwise wait in FIFO."""
        self._runners[operation_id] = factory
        record = await self.get(operation_id)
        if record is None:
            return
        if self._current.get(int(record["server_id"])) == operation_id:
            self._start(operation_id, factory)

    def _start(self, operation_id: str, factory: Any | None = None) -> None:
        existing = self._tasks.get(operation_id)
        if existing is not None and not existing.done():
            return
        runner = factory or self._runners.get(operation_id)
        if runner is None:
            return
        from services.task_registry import action_task_registry

        task = action_task_registry.create(runner())
        self.bind_task(operation_id, task)

    async def list_for_server(self, server_id: int) -> list[dict[str, Any]]:
        """Current job, FIFO waiters, and other in-memory records for this server."""
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        current = await self.get_current(server_id)
        if current:
            items.append(current)
            seen.add(str(current["operation_id"]))
        async with self._lock:
            pending_ids = await self._pending_ids_unlocked(server_id)
            extras = [
                dict(record)
                for record in self._records.values()
                if int(record.get("server_id") or 0) == server_id
            ]
        for operation_id in pending_ids:
            if operation_id in seen:
                continue
            record = await self.get(operation_id)
            if record:
                items.append(record)
                seen.add(operation_id)
        for record in extras:
            operation_id = str(record.get("operation_id") or "")
            if not operation_id or operation_id in seen:
                continue
            items.append(record)
            seen.add(operation_id)
        return items

    async def list_failed_for_server(self, server_id: int) -> list[dict[str, Any]]:
        """Failed jobs still inside the 7-day retention window."""
        async with self._lock:
            failed_ids = await self._failed_ids_unlocked(server_id)
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=FAILED_RETENTION_SECONDS)
        kept: list[str] = []
        items: list[dict[str, Any]] = []
        for operation_id in failed_ids:
            record = await self.get(operation_id)
            if record is None or record.get("status") != "failed":
                continue
            completed = _as_datetime(record.get("completed_at"))
            if completed is not None and completed < cutoff:
                continue
            kept.append(operation_id)
            items.append(record)
        if kept != failed_ids:
            async with self._lock:
                self._failed[server_id] = list(kept)
            await self._persist_failed(server_id)
        items.sort(
            key=lambda item: str(item.get("completed_at") or item.get("started_at") or ""),
            reverse=True,
        )
        return items

    async def dismiss_failed(self, operation_id: str) -> dict[str, Any] | None:
        """Drop one failed job from the inbox so it no longer appears."""
        record = await self.get(operation_id)
        if record is None or record.get("status") != "failed":
            return None
        server_id = int(record["server_id"])
        was_current = False
        async with self._lock:
            failed_ids = await self._failed_ids_unlocked(server_id)
            if operation_id in failed_ids:
                failed_ids = [item for item in failed_ids if item != operation_id]
                self._failed[server_id] = failed_ids
            if self._current.get(server_id) == operation_id:
                self._current.pop(server_id, None)
                was_current = True
        await self._persist_failed(server_id)
        if was_current:
            try:
                await redis_manager.delete(self._current_key(server_id))
            except Exception as exc:
                logger.warning("Unable to clear current pointer for server %s: %s", server_id, exc)
        await self._forget_operation(operation_id)
        return record

    async def clear_failed(self, server_ids: list[int]) -> int:
        """Dismiss every retained failure for the given servers."""
        cleared = 0
        for server_id in server_ids:
            records = await self.list_failed_for_server(server_id)
            for record in records:
                if await self.dismiss_failed(str(record["operation_id"])):
                    cleared += 1
        return cleared

    async def latest_message(self, operation_id: str) -> str | None:
        events = list(self._events.get(operation_id) or [])
        if not events:
            events = await self._load_events(operation_id)
        if events:
            message = str(events[-1].get("message") or "").strip()
            if message:
                return message
        record = await self.get(operation_id)
        if record and record.get("message"):
            return str(record["message"])
        return None

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

    async def cancel(self, operation_id: str, *, message: str) -> dict[str, Any] | None:
        """Force-stop one queued or running operation and retain its failure record."""
        record = await self.get(operation_id)
        if record is None or record.get("status") not in ACTIVE_STATUSES:
            return record

        server_id = int(record["server_id"])
        current = await self.get_current(server_id)
        if current and str(current.get("operation_id")) == operation_id:
            return await self.abort(server_id, message=message)

        # A queued operation can be removed without disturbing the current job.
        # Recheck the current pointer while holding the queue lock so a worker
        # promoted at the same time is cancelled instead of left running.
        task: asyncio.Task | None = None
        became_current = False
        async with self._lock:
            if self._current.get(server_id) == operation_id:
                became_current = True
            else:
                pending = await self._pending_ids_unlocked(server_id)
                if operation_id not in pending:
                    return await self.get(operation_id)
                pending = [item for item in pending if item != operation_id]
                self._pending[server_id] = pending
                self._runners.pop(operation_id, None)
                task = self._tasks.pop(operation_id, None)

        if became_current:
            task = self._tasks.pop(operation_id, None)
        else:
            await self._persist_pending(server_id)
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
        if record is not None:
            await self._promote_next(int(record["server_id"]), operation_id)
            if not success:
                await self._remember_failed(record)
        return record

    async def _promote_next(self, server_id: int, finished_id: str) -> None:
        if self._current.get(server_id) != finished_id:
            return
        next_id = await self._pop_pending(server_id)
        if not next_id:
            return
        self._current[server_id] = next_id
        await redis_manager.set(self._current_key(server_id), next_id, expire=OPERATION_TTL_SECONDS)
        factory = self._runners.get(next_id)
        if factory is not None:
            self._start(next_id, factory)
            return
        await self.finish(
            next_id,
            success=False,
            message="Queued worker was lost after a process restart",
        )

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
            self._events[operation_id] = _trim_events(self._events[operation_id])
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
                        self._events[operation_id] = _trim_events(events)
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

    async def wait_until_terminal(
        self, operation_id: str, *, timeout: float | None = None
    ) -> dict[str, Any]:
        """Block until this operation completes or fails.

        Scheduled tasks and fleet batch jobs enqueue onto the per-server FIFO,
        then wait here so their own status records stay accurate without
        holding the maintenance lock.
        """
        record = await self.get(operation_id)
        if record is None:
            raise LookupError(f"Operation {operation_id} was not found")
        if record.get("status") not in ACTIVE_STATUSES:
            return record
        queue = await self.subscribe_queue(operation_id)
        try:
            record = await self.get(operation_id)
            if record is None:
                raise LookupError(f"Operation {operation_id} was not found")
            if record.get("status") not in ACTIVE_STATUSES:
                return record
            loop = asyncio.get_running_loop()
            deadline = None if timeout is None else loop.time() + timeout
            while True:
                remaining = None
                if deadline is not None:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise TimeoutError(f"Timed out waiting for operation {operation_id}")
                event = await asyncio.wait_for(queue.get(), timeout=remaining)
                if event.get("type") in TERMINAL_EVENT_TYPES:
                    finished = await self.get(operation_id)
                    if finished is None:
                        raise LookupError(f"Operation {operation_id} was not found")
                    return finished
        finally:
            await self.unsubscribe_queue(operation_id, queue)

    async def _pending_ids_unlocked(self, server_id: int) -> list[str]:
        cached = self._pending.get(server_id)
        if cached is not None:
            return list(cached)
        stored = await redis_manager.get(self._pending_key(server_id))
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

    async def _failed_ids_unlocked(self, server_id: int) -> list[str]:
        cached = self._failed.get(server_id)
        if cached is not None:
            return list(cached)
        stored = await redis_manager.get(self._failed_key(server_id))
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
        self._failed[server_id] = ids
        return list(ids)

    async def _persist_failed(self, server_id: int) -> None:
        try:
            await redis_manager.set(
                self._failed_key(server_id),
                list(self._failed.get(server_id) or []),
                expire=FAILED_RETENTION_SECONDS,
            )
        except Exception as exc:
            logger.warning("Unable to persist failed operations for server %s: %s", server_id, exc)

    async def _remember_failed(self, record: dict[str, Any]) -> None:
        server_id = int(record["server_id"])
        operation_id = str(record["operation_id"])
        async with self._lock:
            failed_ids = await self._failed_ids_unlocked(server_id)
            if operation_id not in failed_ids:
                failed_ids.append(operation_id)
            self._failed[server_id] = failed_ids[-MAX_FAILED_PER_SERVER:]
        await self._persist_failed(server_id)
        await self._persist_record(record)
        await self._expire_events(operation_id, FAILED_RETENTION_SECONDS)

    async def _forget_operation(self, operation_id: str) -> None:
        self._records.pop(operation_id, None)
        self._events.pop(operation_id, None)
        self._runners.pop(operation_id, None)
        self._tasks.pop(operation_id, None)
        try:
            await redis_manager.delete(self._record_key(operation_id))
            await redis_manager.delete(self._events_key(operation_id))
        except Exception as exc:
            logger.warning("Unable to delete operation %s: %s", operation_id, exc)

    async def _expire_events(self, operation_id: str, expire: int) -> None:
        try:
            await redis_manager.client.expire(
                redis_manager.prefixed_key(self._events_key(operation_id)), expire
            )
        except Exception as exc:
            logger.warning("Unable to refresh event TTL for %s: %s", operation_id, exc)

    async def _persist_pending(self, server_id: int) -> None:
        try:
            await redis_manager.set(
                self._pending_key(server_id),
                list(self._pending.get(server_id) or []),
                expire=OPERATION_TTL_SECONDS,
            )
        except Exception as exc:
            logger.warning("Unable to persist pending operations for server %s: %s", server_id, exc)

    async def _pop_pending(self, server_id: int) -> str | None:
        async with self._lock:
            pending = await self._pending_ids_unlocked(server_id)
            if not pending:
                return None
            next_id = pending.pop(0)
            self._pending[server_id] = pending
        await self._persist_pending(server_id)
        return next_id

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

    async def patch(self, operation_id: str, **changes: Any) -> dict[str, Any] | None:
        """Persist extra file-job fields such as a resolved download path."""
        allowed = {"destination", "target_path", "archive_path"}
        filtered = {key: value for key, value in changes.items() if key in allowed}
        if not filtered:
            return await self.get(operation_id)
        return await self._update(operation_id, **filtered)

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
                expire=_record_ttl(record),
            )
        except Exception as exc:
            logger.warning(
                "Unable to persist server operation %s: %s", record.get("operation_id"), exc
            )

    async def _persist_event(self, operation_id: str, event: dict[str, Any]) -> None:
        key = redis_manager.prefixed_key(self._events_key(operation_id))
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
            values = await redis_manager.client.lrange(
                redis_manager.prefixed_key(self._events_key(operation_id)), 0, -1
            )
        except Exception as exc:
            logger.warning("Unable to load operation events %s: %s", operation_id, exc)
            return []
        events: list[dict[str, Any]] = []
        for value in values:
            try:
                events.append(json.loads(value))
            except TypeError, json.JSONDecodeError:
                continue
        return _trim_events(events)


server_operation_hub = ServerOperationHub()
