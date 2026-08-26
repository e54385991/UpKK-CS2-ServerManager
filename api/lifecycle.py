"""Application startup and deterministic resource shutdown orchestration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from modules import database as database_module
from modules.database import init_db, migrate_db
from modules.models import Server
from services.container import ServiceContainer, build_service_container
from services.task_registry import shutdown_background_tasks

logger = logging.getLogger(__name__)

# Compatibility export used by diagnostics and tests.
engine = database_module.engine

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


class ApplicationLifecycle:
    """Own one application's successfully started resources."""

    def __init__(self, container: ServiceContainer | None = None) -> None:
        self.container = container or build_service_container()
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

            resources = self.container.resources

            # Shared transports are created at import time. Register their
            # cleanup before database work so failed startup cannot leak them.
            # The stack is reversed during shutdown, yielding the legacy order:
            # SSH pool, Redis, HTTP client, then database engine.
            self._add_cleanup("database engine", resources.database_engine.dispose)
            self._add_cleanup("HTTP client", resources.http.close)
            self._add_cleanup("Redis client", resources.redis.close)

            from services.ai_security import (
                AIConfigurationError,
                initialize_credential_encryption,
            )

            try:
                key_source = initialize_credential_encryption()
                logger.info("AI credential encryption ready (source: %s)", key_source)
            except AIConfigurationError:
                # AI remains disabled, while all non-AI panel functions can start.
                logger.exception("AI credential encryption initialization failed")

            await migrate_db()
            await init_db()

            await resources.ssh_pool.start_cleanup()

            async def close_ssh_pool() -> None:
                await resources.ssh_pool.stop_cleanup()
                await resources.ssh_pool.close_all()

            self._add_cleanup("SSH connection pool", close_ssh_pool)

            logger.info("Clearing old A2S cache")
            try:
                deleted = await resources.redis.delete_by_pattern("a2s:server:*")
                logger.info("Cleared %s old A2S cache entries", deleted or 0)
            except Exception:
                logger.exception("Failed to clear old A2S cache")

            from services.a2s_cache_service import a2s_cache_service
            from services.ai_retention_service import ai_retention_service
            from services.audit_retention_service import audit_retention_service
            from services.auto_update_service import auto_update_service
            from services.discord_bot_manager import discord_bot_manager
            from services.plugin_auto_update_service import plugin_auto_update_service
            from services.scheduled_task_service import scheduled_task_service
            from services.ssh_health_monitor import ssh_health_monitor
            from services.steam_inf_service import steam_inf_service

            await self._start_service(
                "A2S cache service",
                a2s_cache_service.start,
                a2s_cache_service.stop,
            )
            await self._start_service(
                "AI retention service",
                ai_retention_service.start,
                ai_retention_service.stop,
            )
            await self._start_service(
                "audit retention service",
                audit_retention_service.start,
                audit_retention_service.stop,
            )
            await self._start_service(
                "Discord Bot manager",
                discord_bot_manager.start,
                discord_bot_manager.stop,
            )
            await self._start_service(
                "steam.inf cache service",
                steam_inf_service.start,
                steam_inf_service.stop,
            )
            await self._start_service(
                "auto-update service",
                auto_update_service.start,
                auto_update_service.stop,
            )
            await self._start_service(
                "plugin auto-update service",
                plugin_auto_update_service.start,
                plugin_auto_update_service.stop,
            )
            await self._start_service(
                "scheduled task service",
                scheduled_task_service.start,
                scheduled_task_service.stop,
            )
            await self._start_service(
                "SSH health monitor",
                ssh_health_monitor.start,
                ssh_health_monitor.stop,
            )

            from services.server_monitor import server_monitor
            from services.ssh_manager import SSHManager

            self._add_cleanup("server monitor", server_monitor.stop_all)
            async with resources.session_factory() as db:
                monitored_servers = await Server.get_all_with_panel_monitoring(db)
                for server in monitored_servers:
                    server_monitor.start_monitoring(server.id, SSHManager())
                    logger.info(
                        "Started panel monitoring for server %s (%s)",
                        server.id,
                        server.name,
                    )

            self._started = True
            logger.info("CS2 Server Manager started successfully")

    async def stop(self) -> None:
        async with self._lock:
            # Request-created work is not a startup resource, so always attempt
            # its cleanup first, including after a partial startup failure.
            await _cleanup_runtime_tasks()

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


_default_lifecycle = ApplicationLifecycle()


async def start_application() -> None:
    """Compatibility entry point using the process-default lifecycle."""
    await _default_lifecycle.start()


async def stop_application() -> None:
    """Compatibility entry point using the process-default lifecycle."""
    await _default_lifecycle.stop()


@asynccontextmanager
async def application_lifespan(app: FastAPI):
    """Own lifecycle state per application-factory instance."""
    lifecycle = ApplicationLifecycle(app.state.services)
    app.state.lifecycle = lifecycle
    try:
        await lifecycle.start()
        yield
    finally:
        await lifecycle.stop()
