"""A2S resources must remain owned by their application factory instance."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.requests import Request

from api.application import create_app
from api.lifecycle import ApplicationLifecycle
from api.routes import public
from api.routes.servers import monitoring
from cs2_manager.core import Principal
from modules.config import settings as default_settings
from services.a2s_cache_service import A2SCacheService


class _ScalarResult:
    def __init__(self, values: list[int] | None = None) -> None:
        self.values = values or []

    def scalars(self):
        return self

    def all(self):
        return self.values


class _RecordingSession:
    def __init__(self, owner: "_RecordingDatabase") -> None:
        self.owner = owner

    async def __aenter__(self):
        self.owner.session_entries += 1
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        self.owner.session_exits += 1

    async def execute(self, statement):
        self.owner.statements.append(statement)
        return _ScalarResult(self.owner.server_ids)

    async def commit(self):
        self.owner.commits += 1


class _RecordingDatabase:
    def __init__(self, name: str, server_ids: list[int] | None = None) -> None:
        self.engine = object()
        self.name = name
        self.server_ids = server_ids or []
        self.session_entries = 0
        self.session_exits = 0
        self.commits = 0
        self.statements: list[object] = []

    def session_factory(self):
        return _RecordingSession(self)


class _RecordingRedis:
    def __init__(self, name: str) -> None:
        self.name = name
        self.mget_calls: list[list[str]] = []

    async def mget(self, keys: list[str]):
        self.mget_calls.append(keys)
        return [{"source": self.name} for _key in keys]


def _resources(database, redis, http=None):
    return {
        "database": database,
        "redis": redis,
        "http": http or SimpleNamespace(close=AsyncMock()),
        "ssh_pool": SimpleNamespace(),
    }


@pytest.mark.asyncio
async def test_create_app_a2s_services_do_not_share_redis_or_database() -> None:
    first_database = _RecordingDatabase("first")
    second_database = _RecordingDatabase("second")
    first_redis = _RecordingRedis("first")
    second_redis = _RecordingRedis("second")
    first_http = SimpleNamespace(name="first", close=AsyncMock())
    second_http = SimpleNamespace(name="second", close=AsyncMock())
    isolated_settings = default_settings.model_copy()

    first_app = create_app(
        settings=isolated_settings,
        resource_overrides=_resources(first_database, first_redis, first_http),
        lifespan=None,
    )
    second_app = create_app(
        settings=isolated_settings,
        resource_overrides=_resources(second_database, second_redis, second_http),
        lifespan=None,
    )

    first_service = first_app.state.container.services["a2s_cache"]
    second_service = second_app.state.container.services["a2s_cache"]

    assert isinstance(first_service, A2SCacheService)
    assert isinstance(second_service, A2SCacheService)
    assert first_service is not second_service
    assert first_service.redis_adapter is first_redis
    assert second_service.redis_adapter is second_redis
    assert first_service.session_factory == first_database.session_factory
    assert second_service.session_factory == second_database.session_factory
    assert first_service.steam_service.http_adapter is first_http
    assert second_service.steam_service.http_adapter is second_http

    first_cached = await first_service.get_cached_info_many([11])
    second_cached = await second_service.get_cached_info_many([22])
    await first_service._query_all_servers()
    await second_service._query_all_servers()

    assert first_cached == {11: {"source": "first"}}
    assert second_cached == {22: {"source": "second"}}
    assert first_redis.mget_calls == [["a2s:server:11"]]
    assert second_redis.mget_calls == [["a2s:server:22"]]
    assert first_database.session_entries == first_database.session_exits == 1
    assert second_database.session_entries == second_database.session_exits == 1
    assert len(first_database.statements) == 1
    assert len(second_database.statements) == 1


@pytest.mark.asyncio
async def test_isolated_lifecycle_starts_only_its_owned_a2s_service(monkeypatch) -> None:
    database = SimpleNamespace(
        engine=object(),
        session_factory=Mock(),
        close=AsyncMock(),
    )
    redis = SimpleNamespace(
        delete_by_pattern=AsyncMock(return_value=0),
        close=AsyncMock(),
    )
    http = SimpleNamespace(close=AsyncMock())
    ssh_pool = SimpleNamespace(
        start_cleanup=AsyncMock(),
        stop_cleanup=AsyncMock(),
        close_all=AsyncMock(),
    )
    supervisor = SimpleNamespace(start=Mock(), shutdown=AsyncMock())
    app = create_app(
        settings=default_settings.model_copy(),
        resource_overrides={
            "database": database,
            "redis": redis,
            "http": http,
            "ssh_pool": ssh_pool,
            "task_supervisor": supervisor,
        },
        lifespan=None,
    )
    service = app.state.container.services["a2s_cache"]
    start = AsyncMock()
    stop = AsyncMock()
    monkeypatch.setattr(service, "start", start)
    monkeypatch.setattr(service, "stop", stop)
    migration_check = AsyncMock()
    monkeypatch.setattr("api.lifecycle.require_database_current", migration_check)

    from services.a2s_cache_service import a2s_cache_service

    legacy_start = AsyncMock()
    legacy_stop = AsyncMock()
    monkeypatch.setattr(a2s_cache_service, "start", legacy_start)
    monkeypatch.setattr(a2s_cache_service, "stop", legacy_stop)

    lifecycle = ApplicationLifecycle(container=app.state.container)
    await lifecycle.start()
    await lifecycle.stop()

    migration_check.assert_awaited_once_with(database.engine)
    start.assert_awaited_once_with()
    stop.assert_awaited_once_with()
    legacy_start.assert_not_awaited()
    legacy_stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_both_a2s_cache_routes_resolve_the_application_service() -> None:
    database = _RecordingDatabase("route", server_ids=[31])
    redis = _RecordingRedis("route")
    get_many = AsyncMock(return_value={31: {"success": True}})
    get_steam_version = AsyncMock(return_value=None)
    service = SimpleNamespace(
        get_cached_info_many=get_many,
        get_latest_steam_version=get_steam_version,
    )
    app = create_app(
        settings=default_settings.model_copy(),
        resource_overrides={
            **_resources(database, redis),
            "services": {"a2s_cache": service},
        },
        lifespan=None,
    )
    request = Request({"type": "http", "app": app})
    principal = Principal(
        id=7,
        username="owner",
        email="owner@example.com",
        is_admin=False,
    )

    public_session = database.session_factory()
    public_response = await public.get_user_servers_a2s_cache(
        request=request,
        uow=SimpleNamespace(  # type: ignore[arg-type]
            session=public_session,
            commit=public_session.commit,
        ),
        current_user=principal,
    )
    monitoring_session = database.session_factory()
    monitoring_response = await monitoring.get_all_servers_a2s_cache(
        request=request,
        uow=SimpleNamespace(  # type: ignore[arg-type]
            session=monitoring_session,
            commit=monitoring_session.commit,
        ),
        current_user=principal,
    )

    assert public_response["servers"] == {"31": {"success": True}}
    assert monitoring_response["servers"] == {"31": {"success": True}}
    assert get_many.await_args_list[0].args == ([31],)
    assert get_many.await_args_list[1].args == ([31],)
    get_steam_version.assert_awaited_once_with()
    assert database.commits == 2
