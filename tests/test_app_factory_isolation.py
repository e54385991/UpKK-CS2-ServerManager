"""Regression tests for settings-owned application resources."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api.application import create_app
from api.lifecycle import ApplicationLifecycle
from modules.config import settings as default_settings


@pytest.mark.asyncio
async def test_explicit_settings_build_distinct_default_resources() -> None:
    first_settings = default_settings.model_copy(
        update={
            "MYSQL_HOST": "database-one.internal",
            "MYSQL_DATABASE": "manager_one",
            "REDIS_HOST": "redis-one.internal",
            "REDIS_DB": 11,
        }
    )
    second_settings = default_settings.model_copy(
        update={
            "MYSQL_HOST": "database-two.internal",
            "MYSQL_DATABASE": "manager_two",
            "REDIS_HOST": "redis-two.internal",
            "REDIS_DB": 12,
        }
    )

    first = create_app(settings=first_settings, lifespan=None)
    second = create_app(settings=second_settings, lifespan=None)
    first_container = first.state.container
    second_container = second.state.container

    from modules.database import engine as legacy_engine
    from modules.http_helper import http_helper
    from services.redis_manager import redis_manager
    from services.ssh_connection_pool import ssh_connection_pool

    try:
        assert first_container.database is not second_container.database
        assert first_container.database.engine is not legacy_engine
        assert first_container.database.engine.url.host == "database-one.internal"
        assert second_container.database.engine.url.host == "database-two.internal"
        assert first_container.database.engine.url.database == "manager_one"
        assert second_container.database.engine.url.database == "manager_two"

        assert first_container.redis is not second_container.redis
        assert first_container.redis is not redis_manager
        first_redis_options = first_container.redis.client.connection_pool.connection_kwargs
        second_redis_options = second_container.redis.client.connection_pool.connection_kwargs
        assert first_redis_options["host"] == "redis-one.internal"
        assert first_redis_options["db"] == 11
        assert second_redis_options["host"] == "redis-two.internal"
        assert second_redis_options["db"] == 12

        assert first_container.http is not second_container.http
        assert first_container.http is not http_helper
        assert first_container.ssh_pool is not second_container.ssh_pool
        assert first_container.ssh_pool is not ssh_connection_pool
        assert first_container.legacy_runtime is False
        assert second_container.legacy_runtime is False
    finally:
        for container in (first_container, second_container):
            await container.ssh_pool.close_all()
            await container.redis.close()
            await container.http.close()
            await container.database.close()


def test_core_resource_override_disables_legacy_runtime() -> None:
    database = object()
    app = create_app(
        lifespan=None,
        resource_overrides={"database": database},
    )

    assert app.state.container.database is database
    assert app.state.container.legacy_runtime is False
    assert create_app(lifespan=None).state.container.legacy_runtime is True


@pytest.mark.asyncio
async def test_isolated_lifecycle_never_starts_or_stops_legacy_services(
    monkeypatch,
) -> None:
    events: list[str] = []

    def async_event(name: str):
        async def callback(*_args, **_kwargs):
            events.append(name)

        return callback

    database = SimpleNamespace(
        engine=object(),
        close=AsyncMock(side_effect=async_event("database-close")),
        session_factory=Mock(side_effect=AssertionError("legacy monitor queried isolated DB")),
    )
    redis = SimpleNamespace(
        delete_by_pattern=AsyncMock(return_value=0),
        close=AsyncMock(side_effect=async_event("redis-close")),
    )
    http = SimpleNamespace(close=AsyncMock(side_effect=async_event("http-close")))
    pool = SimpleNamespace(
        start_cleanup=AsyncMock(side_effect=async_event("pool-start")),
        stop_cleanup=AsyncMock(side_effect=async_event("pool-stop")),
        close_all=AsyncMock(side_effect=async_event("pool-close")),
    )
    supervisor = SimpleNamespace(start=Mock(), shutdown=AsyncMock())
    container = SimpleNamespace(
        database=database,
        redis=redis,
        http=http,
        ssh_pool=pool,
        task_supervisor=supervisor,
        legacy_runtime=False,
    )

    migration_check = AsyncMock()
    global_task_cleanup = AsyncMock()
    monkeypatch.setattr("api.lifecycle.require_database_current", migration_check)
    monkeypatch.setattr("api.lifecycle._cleanup_runtime_tasks", global_task_cleanup)

    from services.a2s_cache_service import a2s_cache_service
    from services.auto_update_service import auto_update_service
    from services.plugin_auto_update_service import plugin_auto_update_service
    from services.scheduled_task_service import scheduled_task_service
    from services.ssh_health_monitor import ssh_health_monitor
    from services.steam_inf_service import steam_inf_service

    legacy_services = (
        (a2s_cache_service, AsyncMock(), AsyncMock()),
        (steam_inf_service, AsyncMock(), AsyncMock()),
        (auto_update_service, AsyncMock(), AsyncMock()),
        (plugin_auto_update_service, AsyncMock(), AsyncMock()),
        (scheduled_task_service, AsyncMock(), AsyncMock()),
        (ssh_health_monitor, AsyncMock(), AsyncMock()),
    )
    for service, start, stop in legacy_services:
        monkeypatch.setattr(service, "start", start)
        monkeypatch.setattr(service, "stop", stop)

    lifecycle = ApplicationLifecycle(container=container)  # type: ignore[arg-type]
    await lifecycle.start()

    assert lifecycle.started is True
    assert lifecycle.cleanup_names == (
        "database engine",
        "HTTP client",
        "Redis client",
        "SSH connection pool",
    )
    migration_check.assert_awaited_once_with(database.engine)
    for _service, start, _stop in legacy_services:
        start.assert_not_awaited()

    await lifecycle.stop()

    global_task_cleanup.assert_not_awaited()
    for _service, _start, stop in legacy_services:
        stop.assert_not_awaited()
    assert events == [
        "pool-start",
        "pool-stop",
        "pool-close",
        "redis-close",
        "http-close",
        "database-close",
    ]
