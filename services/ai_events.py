"""AI run event fan-out with short Redis replay storage."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

from services.redis_manager import redis_manager

logger = logging.getLogger(__name__)
AI_EVENT_TTL_SECONDS = 24 * 60 * 60
AI_EVENT_LIMIT = 2000
AI_SUBSCRIBER_QUEUE_LIMIT = 256


class AIEventHub:
    def __init__(self) -> None:
        self._clients: dict[str, set[WebSocket]] = defaultdict(set)
        self._queues: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, run_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients[run_id].add(websocket)

    async def unsubscribe(self, run_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            clients = self._clients.get(run_id)
            if clients is None:
                return
            clients.discard(websocket)
            if not clients:
                self._clients.pop(run_id, None)

    async def subscribe_queue(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=AI_SUBSCRIBER_QUEUE_LIMIT)
        async with self._lock:
            self._queues[run_id].add(queue)
        return queue

    async def unsubscribe_queue(self, run_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            queues = self._queues.get(run_id)
            if queues is None:
                return
            queues.discard(queue)
            if not queues:
                self._queues.pop(run_id, None)

    async def emit(
        self, run_id: str, event_type: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        event = {
            "sequence": str(time.time_ns()),
            "run_id": run_id,
            "type": event_type,
            "payload": payload or {},
        }
        key = f"ai:run:{run_id}:events"
        try:
            encoded = json.dumps(event, ensure_ascii=False, default=str)
            pipeline = redis_manager.client.pipeline(transaction=False)
            pipeline.rpush(key, encoded)
            pipeline.ltrim(key, -AI_EVENT_LIMIT, -1)
            pipeline.expire(key, AI_EVENT_TTL_SECONDS)
            await pipeline.execute()
        except Exception as exc:
            logger.warning("Unable to cache AI run event %s: %s", run_id, exc)

        async with self._lock:
            clients = list(self._clients.get(run_id, set()))
            queues = list(self._queues.get(run_id, set()))
        for queue in queues:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("AI SSE subscriber for run %s is not keeping up", run_id)
        failed: list[WebSocket] = []
        for client in clients:
            try:
                await client.send_json(event)
            except Exception:
                failed.append(client)
        for client in failed:
            await self.unsubscribe(run_id, client)
        return event

    async def replay(self, run_id: str, after_sequence: int = 0) -> list[dict[str, Any]]:
        key = f"ai:run:{run_id}:events"
        try:
            values = await redis_manager.client.lrange(key, 0, -1)
        except Exception as exc:
            logger.warning("Unable to replay AI run events %s: %s", run_id, exc)
            return []
        events: list[dict[str, Any]] = []
        for value in values:
            try:
                event = json.loads(value)
            except TypeError, json.JSONDecodeError:
                continue
            if int(event.get("sequence") or 0) > after_sequence:
                events.append(event)
        return events


ai_event_hub = AIEventHub()
