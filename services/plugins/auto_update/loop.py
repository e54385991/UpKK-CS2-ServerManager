"""Auto-update mixins looked up through the public service facade."""

from __future__ import annotations

import logging
from datetime import timezone
from typing import Any, Dict, Optional

from sqlmodel import col, select

from modules.models import Server
from modules.models.servers import DEFAULT_PLUGIN_UPDATE_CHECK_INTERVAL_HOURS
from services.compat import LateBoundModule
from services.redis_manager import redis_manager
from services.server_operation_hub import (
    ACTIVE_STATUSES,
    ServerOperationConflict,
    server_operation_hub,
)

logger = logging.getLogger("services.plugin_auto_update_service")
host = LateBoundModule("services.plugin_auto_update_service")


class AutoUpdateLoopMixin:
    CHECK_LOOP_SECONDS = 60

    def __init__(self) -> None:
        self.task: Optional[Any] = None
        self.running = False
        self._status_cache: Dict[int, Dict[str, Any]] = {}
        self._redis_status_retry_after = 0.0
        self._progress_sinks: Dict[int, Any] = {}

    async def _publish_status(
        self,
        server_id: int,
        *,
        state: Optional[str] = None,
        phase: Optional[str] = None,
        message: Optional[str] = None,
        current: Optional[int] = None,
        total: Optional[int] = None,
        log: Optional[str] = None,
    ) -> Dict[str, Any]:
        status = dict(
            self._status_cache.get(server_id)
            or {
                "state": "idle",
                "phase": "idle",
                "message": "No plugin update task has run yet",
                "current": 0,
                "total": 0,
                "logs": [],
                "started_at": None,
                "finished_at": None,
            }
        )
        if state:
            previous_state = status.get("state")
            status["state"] = state
            if state == "running" and previous_state != "running":
                status["started_at"] = host.get_current_time().isoformat()
                status["finished_at"] = None
                status["logs"] = []
            elif state in {"completed", "failed"}:
                status["finished_at"] = host.get_current_time().isoformat()
        if phase is not None:
            status["phase"] = phase
        if message is not None:
            status["message"] = message
        if current is not None:
            status["current"] = current
        if total is not None:
            status["total"] = total
        if log:
            raw_logs = status.get("logs")
            logs = list(raw_logs) if isinstance(raw_logs, list) else []
            logs.append({"time": host.get_current_time().isoformat(), "message": log})
            status["logs"] = logs[-100:]
        self._status_cache[server_id] = status
        sink = self._progress_sinks.get(server_id)
        if sink is not None:
            line = log or message
            if line:
                await sink(line)
        now = host.asyncio.get_running_loop().time()
        if now >= self._redis_status_retry_after:
            try:
                await host.asyncio.wait_for(
                    redis_manager.set(
                        f"plugin_auto_update:status:{server_id}", status, expire=86400
                    ),
                    timeout=0.2,
                )
            except Exception as exc:
                self._redis_status_retry_after = now + 30
                logger.debug("Redis plugin status write unavailable; using memory cache: %s", exc)
        return status

    async def get_status(self, server_id: int) -> Dict[str, Any]:
        cached = None
        now = host.asyncio.get_running_loop().time()
        if now >= self._redis_status_retry_after:
            try:
                cached = await host.asyncio.wait_for(
                    redis_manager.get(f"plugin_auto_update:status:{server_id}"), timeout=0.2
                )
            except Exception as exc:
                self._redis_status_retry_after = now + 30
                logger.debug("Redis plugin status read unavailable; using memory cache: %s", exc)
        if isinstance(cached, dict):
            self._status_cache[server_id] = cached
            return cached
        return self._status_cache.get(server_id) or {
            "state": "idle",
            "phase": "idle",
            "message": "No plugin update task has run yet",
            "current": 0,
            "total": 0,
            "logs": [],
            "started_at": None,
            "finished_at": None,
        }

    def set_progress_sink(self, server_id: int, sink) -> None:
        """Forward status lines to a hub SSE emitter for one server."""
        self._progress_sinks[server_id] = sink

    def clear_progress_sink(self, server_id: int) -> None:
        self._progress_sinks.pop(server_id, None)

    async def _plugin_update_already_queued(self, server_id: int) -> bool:
        for record in await server_operation_hub.list_for_server(server_id):
            if record.get("status") in ACTIVE_STATUSES and record.get("action") in {
                "plugin_auto_update",
                "plugin_auto_update_test",
            }:
                return True
        return False

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.task = host.asyncio.create_task(self._loop())
        logger.info("Plugin auto-update service started")

    async def stop(self) -> None:
        self.running = False
        if self.task:
            self.task.cancel()
            await host.asyncio.gather(self.task, return_exceptions=True)
        self.task = None

    async def _loop(self) -> None:
        while self.running:
            try:
                await self.check_all_servers()
            except host.asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Plugin auto-update loop failed")
            await host.asyncio.sleep(self.CHECK_LOOP_SECONDS)

    @staticmethod
    def _due(last_check, interval_hours: float) -> bool:
        if last_check is None:
            return True
        now = host.get_current_time()
        if last_check.tzinfo is None:
            last_check = last_check.replace(tzinfo=timezone.utc)
        return (now - last_check).total_seconds() >= interval_hours * 3600

    async def check_all_servers(self) -> None:
        async with host.async_session_maker() as db:
            result = await db.execute(
                select(Server).where(col(Server.enable_plugin_auto_update).is_(True))
            )
            servers = list(result.scalars().all())
        for server in servers:
            if server.should_skip_background_checks():
                continue
            if self._due(
                server.last_plugin_update_check,
                server.plugin_update_check_interval_hours
                or DEFAULT_PLUGIN_UPDATE_CHECK_INTERVAL_HOURS,
            ):
                if await self._plugin_update_already_queued(server.id):
                    continue
                try:
                    from services.operation_enqueue import enqueue_plugin_auto_update

                    await enqueue_plugin_auto_update(
                        server_id=server.id,
                        actor_user_id=server.user_id,
                        force=False,
                    )
                except ServerOperationConflict:
                    logger.info(
                        "Skipping plugin auto-update for server %s: the per-server queue is full",
                        server.id,
                    )
