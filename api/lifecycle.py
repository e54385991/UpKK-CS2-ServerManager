"""Application startup and deterministic resource shutdown orchestration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI

from cs2_manager.core import AppContainer
from cs2_manager.infrastructure.migrations import require_database_current
from modules.database import async_session_maker, engine
from modules.http_helper import http_helper
from modules.models import Server
from services import redis_manager
from services.task_registry import shutdown_background_tasks

logger = logging.getLogger(__name__)

Cleanup = Callable[[], Awaitable[object]]


async def _cleanup_runtime_tasks() -> None:
    """Stop request-created work before closing the services it can use."""
    from services.discord_notification_service import discord_notification_service
    from services.ssh_manager import (
        shutdown_background_tasks as shutdown_ssh_manager_tasks,
    )

    results = await asyncio.gather(
        shutdown_background_tasks(),
        discord_notification_service.shutdown(),
        shutdown_ssh_manager_tasks(),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            logger.error(
                "Runtime task shutdown failed",
                exc_info=(type(result), result, result.__traceback__),
            )


async def _close_ssh_pool(pool=None) -> None:
    if pool is None:
        from services.ssh_connection_pool import ssh_connection_pool

        pool = ssh_connection_pool
    await pool.stop_cleanup()
    await pool.close_all()


class ApplicationLifecycle:
    """Own one application's successfully started resources."""

    def __init__(self, container: AppContainer | None = None) -> None:
        self.container = container
        self._cleanups: list[tuple[str, Cleanup]] = []
        self._started = False
        self._lock = asyncio.Lock()

    @property
    def started(self) -> bool:
        return self._started

    @property
    def cleanup_names(self) -> tuple[str, ...]:
        """Expose deterministic state for diagnostics and unit tests."""
        return tuple(name for name, _cleanup in self._cleanups)

    def _add_cleanup(self, name: str, cleanup: Cleanup) -> None:
        self._cleanups.append((name, cleanup))

    async def _start_service(
        self,
        name: str,
        start: Callable[[], Awaitable[object]],
        stop: Cleanup,
    ) -> None:
        await start()
        self._add_cleanup(name, stop)
        logger.info("%s started", name)

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return

            legacy_runtime = self.container is None or getattr(
                self.container, "legacy_runtime", True
            )
            if self.container is not None:
                start_supervisor = getattr(self.container.task_supervisor, "start", None)
                if start_supervisor is not None:
                    start_supervisor()

            database = self.container.database if self.container else None
            http = self.container.http if self.container else http_helper
            redis = self.container.redis if self.container else redis_manager
            container_services = (
                getattr(self.container, "services", {}) if self.container is not None else {}
            )

            # Register transport cleanup before database work so failed startup
            # cannot leak resources acquired by this application instance.
            # The stack is reversed during shutdown, yielding the legacy order:
            # SSH pool, Redis, HTTP client, then database engine.
            self._add_cleanup(
                "database engine",
                database.close if database else engine.dispose,
            )
            self._add_cleanup("HTTP client", http.close)
            self._add_cleanup("Redis client", redis.close)

            from services.s3_backup_service import S3_BACKUP_SERVICE_KEY

            s3_service = container_services.get(S3_BACKUP_SERVICE_KEY)
            if s3_service is None and legacy_runtime:
                from services.s3_backup_service import s3_backup_service

                s3_service = s3_backup_service
            close_s3 = getattr(s3_service, "close", None)
            if callable(close_s3):
                self._add_cleanup("S3 client cache", close_s3)

            if legacy_runtime:
                # Buffered deployment output must reach Redis before its transport
                # is closed, including after a later startup step fails.
                from services.deployment_progress import flush_deployment_progress

                self._add_cleanup("deployment progress buffer", flush_deployment_progress)

            # Schema changes are an explicit deployment step.  Application
            # processes only verify that Alembic is at repository head, so a
            # second API process can never race startup DDL or legacy adoption.
            await require_database_current(database.engine if database else engine)

            from services.ssh_connection_pool import ssh_connection_pool

            pool = self.container.ssh_pool if self.container else ssh_connection_pool
            if pool is not None:
                await pool.start_cleanup()
                self._add_cleanup(
                    "SSH connection pool",
                    lambda: _close_ssh_pool(pool),
                )

            logger.info("Clearing old A2S cache")
            try:
                deleted = await redis.delete_by_pattern("a2s:server:*")
                logger.info("Cleared %s old A2S cache entries", deleted or 0)
            except Exception:
                logger.exception("Failed to clear old A2S cache")

            from services.a2s_cache_service import A2S_CACHE_SERVICE_KEY

            container_a2s_service = container_services.get(A2S_CACHE_SERVICE_KEY)
            if container_a2s_service is not None:
                await self._start_service(
                    "A2S cache service",
                    container_a2s_service.start,
                    container_a2s_service.stop,
                )
            elif legacy_runtime:
                from services.a2s_cache_service import a2s_cache_service

                await self._start_service(
                    "A2S cache service",
                    a2s_cache_service.start,
                    a2s_cache_service.stop,
                )

            if legacy_runtime:
                from services.auto_update_service import auto_update_service
                from services.maintenance_lock import (
                    MAINTENANCE_LOCK_SERVICE_KEY,
                    maintenance_lock_service,
                )
                from services.plugin_auto_update_service import plugin_auto_update_service
                from services.scheduled_task_service import scheduled_task_service
                from services.ssh_health_monitor import ssh_health_monitor
                from services.ssh_manager import SSHManager
                from services.steam_api_service import SteamAPIService
                from services.steam_inf_service import steam_inf_service

                steam_api_service = SteamAPIService(http_adapter=http)
                runtime_lock_service = container_services.get(
                    MAINTENANCE_LOCK_SERVICE_KEY,
                    maintenance_lock_service,
                )

                def create_ssh_manager() -> SSHManager:
                    if pool is None:
                        raise RuntimeError("SSH connection pool is unavailable")
                    return SSHManager(
                        connection_pool=pool,
                        http_resource=http,
                    )

                await self._start_service(
                    "steam.inf cache service",
                    lambda: steam_inf_service.start(
                        ssh_manager_factory=create_ssh_manager,
                    ),
                    steam_inf_service.stop,
                )
                await self._start_service(
                    "auto-update service",
                    lambda: auto_update_service.start(
                        steam_service=steam_api_service,
                        ssh_manager_factory=create_ssh_manager,
                        lock_service=runtime_lock_service,
                    ),
                    auto_update_service.stop,
                )
                await self._start_service(
                    "plugin auto-update service",
                    lambda: plugin_auto_update_service.start(
                        http_resource=http,
                        ssh_manager_factory=create_ssh_manager,
                        lock_service=runtime_lock_service,
                    ),
                    plugin_auto_update_service.stop,
                )
                await self._start_service(
                    "scheduled task service",
                    lambda: scheduled_task_service.start(
                        http_resource=http,
                        ssh_manager_factory=create_ssh_manager,
                        lock_service=runtime_lock_service,
                        s3_service=s3_service,
                    ),
                    scheduled_task_service.stop,
                )
                await self._start_service(
                    "SSH health monitor",
                    ssh_health_monitor.start,
                    ssh_health_monitor.stop,
                )

                from services.server_monitor import server_monitor

                self._add_cleanup("server monitor", server_monitor.stop_all)
                session_factory = (
                    self.container.database.session_factory
                    if self.container
                    else async_session_maker
                )
                async with session_factory() as db:
                    monitored_servers = await Server.get_all_with_panel_monitoring(db)
                    for server in monitored_servers:
                        server_monitor.start_monitoring(server.id, create_ssh_manager())
                        logger.info(
                            "Started panel monitoring for server %s (%s)",
                            server.id,
                            server.name,
                        )
            else:
                logger.warning(
                    "Remaining legacy background services are disabled for an "
                    "isolated application container until they accept injected resources"
                )

            self._started = True
            logger.info("CS2 Server Manager started successfully")

    async def stop(self) -> None:
        async with self._lock:
            # Request-created work is not a startup resource, so always attempt
            # its cleanup first, including after a partial startup failure.
            legacy_runtime = self.container is None or getattr(
                self.container, "legacy_runtime", True
            )
            if legacy_runtime:
                await _cleanup_runtime_tasks()
            if self.container is not None:
                try:
                    await self.container.task_supervisor.shutdown()
                except BaseException as exc:
                    logger.error(
                        "Task supervisor shutdown failed",
                        exc_info=(type(exc), exc, exc.__traceback__),
                    )

            while self._cleanups:
                name, cleanup = self._cleanups.pop()
                try:
                    await cleanup()
                except BaseException as exc:
                    logger.error(
                        "%s shutdown failed",
                        name,
                        exc_info=(type(exc), exc, exc.__traceback__),
                    )

            self._started = False
            logger.info("CS2 Server Manager shutdown complete")


def _resolve_app(app: FastAPI | None) -> FastAPI:
    if app is not None:
        return app
    # Lazy import preserves the old no-argument helper without recreating a
    # process-global lifecycle alongside the factory-owned one.
    from main import app as default_app

    return default_app


async def start_application(app: FastAPI | None = None) -> None:
    """Compatibility entry point using the lifecycle owned by ``app``."""
    app = _resolve_app(app)
    lifecycle = getattr(app.state, "lifecycle", None)
    if lifecycle is None:
        lifecycle = ApplicationLifecycle(container=getattr(app.state, "container", None))
        app.state.lifecycle = lifecycle
    await lifecycle.start()


async def stop_application(app: FastAPI | None = None) -> None:
    """Stop the compatibility lifecycle owned by ``app``, when present."""
    app = _resolve_app(app)
    lifecycle = getattr(app.state, "lifecycle", None)
    if lifecycle is not None:
        await lifecycle.stop()


# Preserve the historical import while making ``cs2_manager.runtime`` the
# canonical home of the ASGI lifespan.
from cs2_manager.runtime.lifespan import (  # noqa: E402
    application_lifespan as application_lifespan,
)
