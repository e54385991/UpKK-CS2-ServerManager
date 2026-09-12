"""Redis + in-memory history for one process instance."""

from __future__ import annotations

import json
import logging
from collections import deque
from contextlib import asynccontextmanager
from time import time
from typing import Any

from modules.observability import (
    instance_id,
    is_monitor_io,
    record_redis,
    reset_monitor_io,
    set_monitor_io,
)
from services.redis_manager import redis_manager

logger = logging.getLogger(__name__)

RETENTION_SECONDS = 24 * 60 * 60
TTL_SECONDS = 25 * 60 * 60
MAX_POINTS = 8640
MAX_ERRORS = 10_000
MEMORY_POINTS = 90
MEMORY_ERRORS = 1000
MEMORY_SECONDS = 15 * 60

_POINTS_KEY = "panel_mon:{instance}:pts"
_ERRORS_KEY = "panel_mon:{instance}:err"
_SNAP_KEY = "panel_mon:{instance}:snap"
_INSTANCES_KEY = "panel_mon:instances"


class MonitorHistory:
    def __init__(self) -> None:
        self.history_available = True
        self.history_error: str | None = None
        self.last_sample_at: float | None = None
        self.last_collection_ms: float | None = None
        self.dropped_errors = 0
        self.truncated_summaries = 0
        self._memory_points: deque[dict[str, Any]] = deque(maxlen=MEMORY_POINTS)
        self._memory_errors: deque[dict[str, Any]] = deque(maxlen=MEMORY_ERRORS)
        self._snapshot: dict[str, Any] | None = None
        self._pending_points: deque[dict[str, Any]] = deque(maxlen=MEMORY_POINTS)
        self._pending_errors: deque[dict[str, Any]] = deque(maxlen=MEMORY_ERRORS)

    def remember_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._snapshot = snapshot
        self.last_sample_at = time()

    def cached_snapshot(self) -> dict[str, Any] | None:
        return self._snapshot

    async def write_point(self, point: dict[str, Any]) -> None:
        self._memory_points.append(point)
        self.last_sample_at = float(point.get("t") or time())
        if await self._persist_point(point):
            await self._flush_pending()
            return
        self._pending_points.append(point)

    async def write_errors(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        persisted = await self._persist_errors(events)
        for event in events:
            if event.get("truncated"):
                self.truncated_summaries += 1
            memory_limit = self._memory_errors.maxlen
            if (
                not persisted
                and memory_limit is not None
                and len(self._memory_errors) >= memory_limit
            ):
                self.dropped_errors += 1
            self._memory_errors.append(event)
            if not persisted:
                self._pending_errors.append(event)
        if persisted:
            await self._flush_pending()

    async def load_points(
        self, *, instance: str, start_ts: float, end_ts: float
    ) -> list[dict[str, Any]]:
        stored = await self._load_zset(_POINTS_KEY.format(instance=instance), start_ts, end_ts)
        if stored is not None:
            return stored
        if instance != instance_id():
            return []
        return [
            point
            for point in self._memory_points
            if start_ts <= float(point.get("t") or 0) <= end_ts
        ]

    async def load_errors(
        self, *, instance: str, start_ts: float, end_ts: float
    ) -> list[dict[str, Any]]:
        stored = await self._load_zset(_ERRORS_KEY.format(instance=instance), start_ts, end_ts)
        if stored is not None:
            return stored
        if instance != instance_id():
            return []
        return [
            event
            for event in self._memory_errors
            if start_ts <= float(event.get("ts") or 0) <= end_ts
        ]

    async def load_snapshot(self, instance: str) -> dict[str, Any] | None:
        if instance == instance_id() and self._snapshot is not None:
            return self._snapshot
        async with _monitor_io():
            try:
                raw = await redis_manager.client.get(
                    redis_manager.prefixed_key(_SNAP_KEY.format(instance=instance))
                )
            except Exception as exc:
                self._mark_unavailable(exc)
                return self._snapshot if instance == instance_id() else None
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    async def write_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.remember_snapshot(snapshot)
        encoded = json.dumps(snapshot, default=str)
        async with _monitor_io():
            try:
                await redis_manager.client.set(
                    redis_manager.prefixed_key(_SNAP_KEY.format(instance=instance_id())),
                    encoded,
                    ex=TTL_SECONDS,
                )
                self._mark_available()
            except Exception as exc:
                self._mark_unavailable(exc)

    async def list_instances(self) -> list[tuple[str, float]]:
        async with _monitor_io():
            try:
                raw = await redis_manager.client.zrangebyscore(
                    redis_manager.prefixed_key(_INSTANCES_KEY),
                    time() - RETENTION_SECONDS,
                    time() + 60,
                    withscores=True,
                )
            except Exception as exc:
                self._mark_unavailable(exc)
                return [(instance_id(), self.last_sample_at or time())]
        items: list[tuple[str, float]] = []
        for member, score in raw or []:
            items.append((str(member), float(score)))
        current = instance_id()
        if not any(item[0] == current for item in items):
            items.append((current, self.last_sample_at or time()))
        return items

    def clear_buffers(self) -> None:
        self._memory_points.clear()
        self._memory_errors.clear()
        self._pending_points.clear()
        self._pending_errors.clear()

    async def _persist_point(self, point: dict[str, Any]) -> bool:
        encoded = json.dumps(point, separators=(",", ":"), default=str)
        ok = await self._zadd(
            _POINTS_KEY.format(instance=instance_id()),
            float(point["t"]),
            encoded,
            MAX_POINTS,
        )
        if ok:
            await self._touch_instance(float(point["t"]))
        return ok

    async def _persist_errors(self, events: list[dict[str, Any]]) -> bool:
        key = _ERRORS_KEY.format(instance=instance_id())
        ok = True
        for event in events:
            member = json.dumps(event, separators=(",", ":"), default=str)
            if not await self._zadd(
                key,
                float(event["ts"]),
                member,
                MAX_ERRORS,
                count_drops=True,
            ):
                ok = False
        return ok

    async def _flush_pending(self) -> None:
        if not self.history_available:
            return
        while self._pending_points:
            point = self._pending_points[0]
            if time() - float(point.get("t") or 0) > MEMORY_SECONDS:
                self._pending_points.popleft()
                continue
            if not await self._persist_point(point):
                return
            self._pending_points.popleft()
        while self._pending_errors:
            event = self._pending_errors[0]
            if not await self._persist_errors([event]):
                return
            self._pending_errors.popleft()

    async def _zadd(
        self,
        key: str,
        score: float,
        member: str,
        max_items: int,
        *,
        count_drops: bool = False,
    ) -> bool:
        async with _monitor_io():
            try:
                namespaced = redis_manager.prefixed_key(key)
                client = redis_manager.client
                await client.zadd(namespaced, {member: score})
                await client.zremrangebyscore(namespaced, 0, time() - RETENTION_SECONDS)
                removed = await client.zremrangebyrank(namespaced, 0, -(max_items + 1))
                if count_drops and isinstance(removed, int) and removed > 0:
                    self.dropped_errors += removed
                await client.expire(namespaced, TTL_SECONDS)
                self._mark_available()
                return True
            except Exception as exc:
                self._mark_unavailable(exc)
                return False

    async def _load_zset(
        self, key: str, start_ts: float, end_ts: float
    ) -> list[dict[str, Any]] | None:
        async with _monitor_io():
            try:
                raw = await redis_manager.client.zrangebyscore(
                    redis_manager.prefixed_key(key),
                    start_ts,
                    end_ts,
                )
                self._mark_available()
            except Exception as exc:
                self._mark_unavailable(exc)
                return None
        items: list[dict[str, Any]] = []
        for item in raw or []:
            if not isinstance(item, str | bytes | bytearray):
                continue
            try:
                parsed = json.loads(item)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                items.append(parsed)
        return items

    async def _touch_instance(self, ts: float) -> None:
        async with _monitor_io():
            try:
                key = redis_manager.prefixed_key(_INSTANCES_KEY)
                await redis_manager.client.zadd(key, {instance_id(): ts})
                await redis_manager.client.zremrangebyscore(key, 0, time() - RETENTION_SECONDS)
                await redis_manager.client.expire(key, TTL_SECONDS)
            except Exception as exc:
                self._mark_unavailable(exc)

    def _mark_available(self) -> None:
        self.history_available = True
        self.history_error = None

    def _mark_unavailable(self, exc: BaseException) -> None:
        self.history_available = False
        self.history_error = type(exc).__name__
        logger.debug("Panel monitor history storage failed", exc_info=exc)


@asynccontextmanager
async def _monitor_io():
    token = set_monitor_io(True)
    try:
        yield
    finally:
        reset_monitor_io(token)
        if not is_monitor_io():
            record_redis(monitor=True)


history = MonitorHistory()
