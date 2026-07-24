"""Deployment progress fan-out shared by HTTP, WebSocket and services."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import WebSocket

from modules.utils import get_current_time
from services.redis_manager import redis_manager

DEPLOYMENT_PROGRESS_FLUSH_INTERVAL = 0.075
DEPLOYMENT_PROGRESS_BATCH_BYTES = 32 * 1024
DEPLOYMENT_WS_MAX_PENDING_OUTPUT = 128


class _TaskSupervisor(Protocol):
    """Minimal task-owner interface used without coupling services to FastAPI."""

    def create(
        self,
        coroutine: Coroutine[Any, Any, Any],
        *,
        name: str | None = None,
    ) -> asyncio.Task[Any]: ...


@dataclass(slots=True)
class _WebSocketSender:
    websocket: WebSocket
    queue: deque[dict[str, Any]] = field(default_factory=deque)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    output_count: int = 0
    task: asyncio.Task[None] | None = None


class DeploymentWebSocket:
    """Track active deployment WebSockets by server."""

    def __init__(self) -> None:
        self.active_connections: dict[int, list[WebSocket]] = {}
        self._senders: dict[WebSocket, _WebSocketSender] = {}

    async def connect(
        self,
        websocket: WebSocket,
        server_id: int,
        *,
        task_supervisor: _TaskSupervisor | None = None,
    ) -> None:
        await websocket.accept()
        self.active_connections.setdefault(server_id, []).append(websocket)
        sender = _WebSocketSender(websocket=websocket)
        self._senders[websocket] = sender
        send_coroutine = self._send_loop(server_id, sender)
        task_name = f"deployment-ws-{server_id}"
        if task_supervisor is not None:
            sender.task = task_supervisor.create(send_coroutine, name=task_name)
        else:
            sender.task = asyncio.create_task(send_coroutine, name=task_name)
        sender.task.add_done_callback(
            lambda completed: self._sender_finished(
                websocket,
                server_id,
                completed,
            )
        )

    def _sender_finished(
        self,
        websocket: WebSocket,
        server_id: int,
        completed: asyncio.Task[Any],
    ) -> None:
        """Forget a sender only when the completing task still owns its slot."""
        sender = self._senders.get(websocket)
        if sender is None or sender.task is not completed:
            return
        self._senders.pop(websocket, None)
        connections = self.active_connections.get(server_id)
        if connections and websocket in connections:
            connections.remove(websocket)
            if not connections:
                self.active_connections.pop(server_id, None)

    def disconnect(self, websocket: WebSocket, server_id: int) -> None:
        connections = self.active_connections.get(server_id)
        if connections and websocket in connections:
            connections.remove(websocket)
        if connections is not None and not connections:
            self.active_connections.pop(server_id, None)
        sender = self._senders.pop(websocket, None)
        if sender is not None and sender.task is not None:
            try:
                current_task = asyncio.current_task()
            except RuntimeError:
                current_task = None
            if sender.task is not current_task and not sender.task.done():
                sender.task.cancel()

    async def send_message(self, server_id: int, message: dict) -> None:
        """Queue a message without letting a slow socket block producers.

        Output is lossy under backpressure. State transitions are never
        discarded and remove queued output so a completion cannot be followed
        by stale console lines.
        """
        for websocket in tuple(self.active_connections.get(server_id, ())):
            sender = self._senders.get(websocket)
            if sender is None:
                continue
            self._enqueue(sender, message)

    @staticmethod
    def _drop_pending_output(sender: _WebSocketSender) -> None:
        if sender.output_count == 0:
            return
        sender.queue = deque(queued for queued in sender.queue if queued.get("type") != "output")
        sender.output_count = 0

    def _enqueue(self, sender: _WebSocketSender, message: dict[str, Any]) -> None:
        if message.get("type") == "output":
            if sender.output_count >= DEPLOYMENT_WS_MAX_PENDING_OUTPUT:
                # Retain the most recent console output by removing the oldest
                # queued output item. Critical messages are never candidates.
                for index, queued in enumerate(sender.queue):
                    if queued.get("type") == "output":
                        del sender.queue[index]
                        sender.output_count -= 1
                        break
            sender.output_count += 1
        else:
            self._drop_pending_output(sender)
        sender.queue.append(message)
        sender.ready.set()

    async def _send_loop(self, server_id: int, sender: _WebSocketSender) -> None:
        try:
            while True:
                await sender.ready.wait()
                while sender.queue:
                    message = sender.queue.popleft()
                    if message.get("type") == "output":
                        sender.output_count = max(0, sender.output_count - 1)
                    if not sender.queue:
                        sender.ready.clear()
                    await asyncio.wait_for(
                        sender.websocket.send_json(message),
                        timeout=2.0,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            self.disconnect(sender.websocket, server_id)


deployment_ws = DeploymentWebSocket()


class DeploymentProgressBuffer:
    """Coalesce high-volume output while persisting state transitions immediately."""

    def __init__(
        self,
        flush_interval: float = DEPLOYMENT_PROGRESS_FLUSH_INTERVAL,
        max_batch_bytes: int = DEPLOYMENT_PROGRESS_BATCH_BYTES,
    ) -> None:
        self.flush_interval = flush_interval
        self.max_batch_bytes = max_batch_bytes
        self._pending: dict[int, list[dict[str, str]]] = {}
        self._pending_bytes: dict[int, int] = {}
        self._flush_tasks: dict[int, asyncio.Task[None]] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    async def append(self, server_id: int, entry: dict[str, str]) -> None:
        """Buffer output entries; flush non-output state changes without delay."""
        if entry["type"] != "output":
            await self.flush(server_id)
            await redis_manager.append_deployment_progress_batch(server_id, [entry])
            return

        lock = self._locks.setdefault(server_id, asyncio.Lock())
        should_flush = False
        async with lock:
            self._pending.setdefault(server_id, []).append(entry)
            self._pending_bytes[server_id] = self._pending_bytes.get(server_id, 0) + len(
                entry["message"].encode("utf-8")
            )
            task = self._flush_tasks.get(server_id)
            if task is None or task.done():
                self._flush_tasks[server_id] = asyncio.create_task(self._flush_later(server_id))
            should_flush = self._pending_bytes[server_id] >= self.max_batch_bytes

        if should_flush:
            await self.flush(server_id)

    async def _flush_later(self, server_id: int) -> None:
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(self.flush_interval)
            await self.flush(server_id)
        finally:
            if self._flush_tasks.get(server_id) is current_task:
                self._flush_tasks.pop(server_id, None)

    async def flush(self, server_id: int) -> None:
        """Persist one server's buffered output in FIFO order."""
        lock = self._locks.setdefault(server_id, asyncio.Lock())
        async with lock:
            entries = self._pending.pop(server_id, [])
            self._pending_bytes.pop(server_id, None)
            if entries:
                await redis_manager.append_deployment_progress_batch(server_id, entries)

    async def flush_all(self) -> None:
        """Flush all pending servers for graceful shutdown and tests."""
        server_ids = list(self._pending)
        if server_ids:
            await asyncio.gather(*(self.flush(server_id) for server_id in server_ids))

    async def close(self) -> None:
        """Flush buffered output and stop delayed flush tasks."""
        await self.flush_all()
        tasks = [task for task in self._flush_tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._flush_tasks.clear()


deployment_progress_buffer = DeploymentProgressBuffer()


async def send_deployment_update(
    server_id: int,
    msg_type: str,
    message: str,
) -> None:
    """Send a live update and persist it for reconnect recovery."""
    timestamp = get_current_time().isoformat()
    await deployment_ws.send_message(
        server_id,
        {
            "type": msg_type,
            "message": message,
            "timestamp": timestamp,
        },
    )
    await deployment_progress_buffer.append(
        server_id,
        {"type": msg_type, "message": message, "timestamp": timestamp},
    )


async def flush_deployment_progress() -> None:
    """Flush buffered output; intended for application shutdown."""
    await deployment_progress_buffer.close()
