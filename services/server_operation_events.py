"""Focused helpers; mutable state remains owned by the original controller."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from .server_operation_dependencies import OperationDependencies

if TYPE_CHECKING:
    from .server_operation_hub import ServerOperationHub


async def latest_message(
    self: ServerOperationHub, dependencies: OperationDependencies, operation_id: str
) -> str | None:
    from services.operations.inbox_messages import last_event_text, read_latest_messages

    message = last_event_text(self._events.get(operation_id))
    if message:
        return message
    stored = await read_latest_messages(
        dependencies.redis_manager, {operation_id: self._events_key(operation_id)}
    )
    if stored.get(operation_id):
        return stored[operation_id]
    record = await self.get(operation_id)
    if record and record.get("message"):
        return str(record["message"]).strip() or None
    return None


async def replay(
    self: ServerOperationHub,
    dependencies: OperationDependencies,
    operation_id: str,
    after_sequence: int = 0,
) -> list[dict[str, Any]]:
    events = list(self._events.get(operation_id) or [])
    if not events:
        events = await self._load_events(operation_id)
        if events:
            async with self._lock:
                if not self._events.get(operation_id):
                    self._events[operation_id] = dependencies.trim_events(events)
    replayed: list[dict[str, Any]] = []
    for event in events:
        try:
            sequence = int(event.get("sequence") or 0)
        except TypeError, ValueError:
            continue
        if sequence > after_sequence:
            replayed.append(event)
    return replayed


async def subscribe_queue(
    self: ServerOperationHub, dependencies: OperationDependencies, operation_id: str
) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=dependencies.queue_limit)
    async with self._lock:
        self._queues[operation_id].add(queue)
    return queue


async def unsubscribe_queue(
    self: ServerOperationHub,
    dependencies: OperationDependencies,
    operation_id: str,
    queue: asyncio.Queue,
) -> None:
    async with self._lock:
        queues = self._queues.get(operation_id)
        if queues is None:
            return
        queues.discard(queue)
        if not queues:
            self._queues.pop(operation_id, None)


async def wait_until_terminal(
    self: ServerOperationHub,
    dependencies: OperationDependencies,
    operation_id: str,
    *,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Block until this operation completes or fails.

    Scheduled tasks and fleet batch jobs enqueue onto the per-server FIFO,
    then wait here so their own status records stay accurate without
    holding the maintenance lock.
    """
    record = await self.get(operation_id)
    if record is None:
        raise LookupError(f"Operation {operation_id} was not found")
    if record.get("status") not in dependencies.active_statuses:
        return record
    queue = await self.subscribe_queue(operation_id)
    try:
        record = await self.get(operation_id)
        if record is None:
            raise LookupError(f"Operation {operation_id} was not found")
        if record.get("status") not in dependencies.active_statuses:
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
            if event.get("type") in dependencies.terminal_event_types:
                finished = await self.get(operation_id)
                if finished is None:
                    raise LookupError(f"Operation {operation_id} was not found")
                return finished
    finally:
        await self.unsubscribe_queue(operation_id, queue)
