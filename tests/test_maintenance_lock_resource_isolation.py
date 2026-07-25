"""Maintenance locks must use the Redis resource owned by each application."""

from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute

from api.application import create_app, register_exception_handlers
from api.dependencies import resolve_maintenance_lock_service
from api.routes import map_management, plugin_auto_update, plugin_configs
from modules import get_current_active_user, get_db
from modules.config import settings as default_settings
from services import maintenance_lock as maintenance_module
from services.maintenance_lock import (
    MAINTENANCE_LOCK_SERVICE_KEY,
    MaintenanceLockService,
    maintenance_lock_service,
)


class _LockRedis:
    def __init__(self, name: str, *, refresh_result: bool = True) -> None:
        self.name = name
        self.refresh_result = refresh_result
        self.acquire_calls: list[tuple[str, str, int]] = []
        self.release_calls: list[tuple[str, str]] = []

    async def acquire_lock(self, key: str, token: str, expire: int) -> bool:
        self.acquire_calls.append((key, token, expire))
        return True

    async def is_lock_held(self, key: str) -> bool:
        del key
        return False

    async def refresh_lock(self, key: str, token: str, expire: int) -> bool:
        del key, token, expire
        return self.refresh_result

    async def release_lock(self, key: str, token: str) -> bool:
        self.release_calls.append((key, token))
        return True


class _RouteLockService:
    def __init__(self, name: str, *, locked: bool = False) -> None:
        self.name = name
        self.locked = locked
        self.status_calls: list[int] = []
        self.get_calls: list[tuple[int, str]] = []

    def get(self, server_id: int, *, operation: str = "maintenance", **_kwargs):
        self.get_calls.append((server_id, operation))

        class Context:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        return Context()

    async def is_locked(self, server_id: int) -> bool:
        self.status_calls.append(server_id)
        return self.locked


class _RouteHTTP:
    def __init__(self, name: str) -> None:
        self.name = name
        self.is_closed = False

    async def get(self, *_args, **_kwargs):
        raise AssertionError("the closed test task must not issue HTTP")

    async def post(self, *_args, **_kwargs):
        raise AssertionError("the closed test task must not issue HTTP")

    async def download_file(self, *_args, **_kwargs):
        raise AssertionError("the closed test task must not issue HTTP")

    @asynccontextmanager
    async def borrow_client(self):
        yield SimpleNamespace()


def _resources(redis: _LockRedis) -> dict[str, object]:
    return {
        "database": SimpleNamespace(session_factory=object()),
        "redis": redis,
        "http": SimpleNamespace(close=AsyncMock()),
        "ssh_pool": SimpleNamespace(),
    }


@pytest.mark.asyncio
async def test_factory_apps_bind_distinct_maintenance_lock_redis() -> None:
    first_redis = _LockRedis("first")
    second_redis = _LockRedis("second")
    isolated_settings = default_settings.model_copy()

    first_app = create_app(
        settings=isolated_settings,
        resource_overrides=_resources(first_redis),
        lifespan=None,
    )
    second_app = create_app(
        settings=isolated_settings,
        resource_overrides=_resources(second_redis),
        lifespan=None,
    )

    first_service = first_app.state.container.services[MAINTENANCE_LOCK_SERVICE_KEY]
    second_service = second_app.state.container.services[MAINTENANCE_LOCK_SERVICE_KEY]

    assert isinstance(first_service, MaintenanceLockService)
    assert isinstance(second_service, MaintenanceLockService)
    assert first_service is not second_service
    assert first_service.redis_adapter is first_redis
    assert second_service.redis_adapter is second_redis

    async with first_service.get(11, wait=False):
        pass
    async with second_service.get(22, wait=False):
        pass

    assert first_redis.acquire_calls[0][0] == "server_operation_lock:11"
    assert second_redis.acquire_calls[0][0] == "server_operation_lock:22"
    assert len(first_redis.release_calls) == len(second_redis.release_calls) == 1


@pytest.mark.asyncio
async def test_missing_application_lock_service_fails_closed_with_503() -> None:
    app = FastAPI()
    app.state.container = SimpleNamespace(services={})
    register_exception_handlers(app)

    @app.post("/destructive")
    async def destructive(
        _service: MaintenanceLockService = Depends(resolve_maintenance_lock_service),
    ):
        return {"unexpected": True}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/destructive")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Operation coordination is unavailable; refusing destructive operation"
    }


@pytest.mark.asyncio
async def test_lost_distributed_lease_cancels_destructive_owner(monkeypatch) -> None:
    redis = _LockRedis("lease-loss", refresh_result=False)
    service = MaintenanceLockService(redis)
    entered = asyncio.Event()
    never = asyncio.Event()

    async def no_delay(_seconds: float) -> None:
        return None

    monkeypatch.setattr(maintenance_module.asyncio, "sleep", no_delay)

    async def destructive_operation() -> None:
        async with service.get(71, wait=False, ttl=3):
            entered.set()
            await never.wait()

    task = asyncio.create_task(destructive_operation())
    await entered.wait()

    with pytest.raises(asyncio.CancelledError, match="Distributed operation lock was lost"):
        await task
    assert redis.release_calls


def test_legacy_factory_keeps_shared_maintenance_lock_facade() -> None:
    app = create_app(lifespan=None)

    assert app.state.container.services[MAINTENANCE_LOCK_SERVICE_KEY] is maintenance_lock_service


def test_mutating_routes_declare_the_application_lock_dependency() -> None:
    expected = {
        map_management.run_custom_map_sync,
        map_management.uninstall_mapchooser_plugin,
        map_management.update_mapchooser_plugin_config,
        map_management.update_maps_config,
        map_management.apply_map_preset,
        map_management.add_map,
        map_management.update_map_enabled,
        map_management.delete_map,
        plugin_configs.save_config_file,
        plugin_auto_update.run_now,
        plugin_auto_update.test_plugin_update,
    }
    routes = [
        route
        for router in (
            map_management.router,
            plugin_configs.router,
            plugin_auto_update.router,
        )
        for route in router.routes
        if isinstance(route, APIRoute) and route.endpoint in expected
    ]

    assert {route.endpoint for route in routes} == expected
    for route in routes:
        assert resolve_maintenance_lock_service in {
            dependency.call for dependency in route.dependant.dependencies
        }
        source = inspect.getsource(route.endpoint)
        assert "maintenance_lock_service.get(" not in source
        assert "maintenance_lock_service.is_locked(" not in source


def test_direct_python_route_facades_keep_the_legacy_lock_service(monkeypatch) -> None:
    for module in (map_management, plugin_configs, plugin_auto_update):
        legacy = _RouteLockService(module.__name__)
        explicit = _RouteLockService(f"{module.__name__}-explicit")
        monkeypatch.setattr(module, "maintenance_lock_service", legacy)

        assert module._maintenance_locks(module._DIRECT_MAINTENANCE_LOCK) is legacy
        assert module._maintenance_locks(explicit) is explicit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("router", "method", "path", "body"),
    (
        (
            map_management.router,
            "PUT",
            "/servers/1/maps",
            {"content": '"Maplist"\\n{\\n}\\n'},
        ),
        (
            plugin_configs.router,
            "PUT",
            "/servers/1/plugin-configs/sources/1/file",
            {
                "path": "cs2/game/csgo/cfg/server.cfg",
                "expected_revision": "0" * 64,
                "mode": "raw",
                "content": "hostname test",
            },
        ),
        (
            plugin_auto_update.router,
            "POST",
            "/api/servers/1/plugin-auto-update/run",
            None,
        ),
    ),
)
async def test_target_routes_fail_closed_without_an_application_lock(
    router,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    app = FastAPI()
    app.state.container = SimpleNamespace(services={})
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        id=7,
        is_admin=False,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.request(method, path, json=body)

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Operation coordination is unavailable; refusing destructive operation"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("http_resource", "ssh_pool", "expected_detail"),
    (
        (None, SimpleNamespace(name="pool"), "Outbound HTTP client is unavailable"),
        (_RouteHTTP("available"), None, "SSH connection pool is unavailable"),
    ),
)
async def test_plugin_update_route_fails_closed_before_spawning_without_transports(
    monkeypatch,
    http_resource: object,
    ssh_pool: object,
    expected_detail: str,
) -> None:
    lock_service = _RouteLockService("application")
    app = FastAPI()
    app.state.container = SimpleNamespace(
        services={MAINTENANCE_LOCK_SERVICE_KEY: lock_service},
        http=http_resource,
        ssh_pool=ssh_pool,
    )
    app.include_router(plugin_auto_update.router)
    database = SimpleNamespace(commit=AsyncMock())
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        id=7,
        is_admin=False,
    )
    monkeypatch.setattr(plugin_auto_update, "owned_server", AsyncMock(return_value=object()))
    spawn = Mock()
    monkeypatch.setattr(plugin_auto_update, "_spawn_plugin_update_task", spawn)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/api/servers/31/plugin-auto-update/run")

    assert response.status_code == 503
    assert response.json() == {"detail": expected_detail}
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_two_apps_use_only_their_lock_for_plugin_update_route(monkeypatch) -> None:
    first_lock = _RouteLockService("first")
    second_lock = _RouteLockService("second")
    first_http = _RouteHTTP("first")
    second_http = _RouteHTTP("second")
    first_pool = SimpleNamespace(name="first")
    second_pool = SimpleNamespace(name="second")
    runtime_calls: list[tuple[int, object, object, object]] = []

    class ForbiddenGlobal:
        def get(self, *_args, **_kwargs):
            raise AssertionError("ASGI route used the process-global lock")

        async def is_locked(self, _server_id: int) -> bool:
            raise AssertionError("ASGI route used the process-global lock")

    monkeypatch.setattr(plugin_auto_update, "maintenance_lock_service", ForbiddenGlobal())
    monkeypatch.setattr(plugin_auto_update, "owned_server", AsyncMock(return_value=object()))

    def check_server(
        server_id: int,
        *,
        http_resource: object,
        ssh_manager_factory,
        lock_service: object,
        **_kwargs,
    ):
        runtime_calls.append((server_id, http_resource, ssh_manager_factory, lock_service))

        async def completed():
            return None

        return completed()

    monkeypatch.setattr(
        plugin_auto_update.plugin_auto_update_service,
        "check_server",
        check_server,
    )

    def spawn(_request, coroutine, *, name: str):
        del name
        coroutine.close()
        return Mock()

    monkeypatch.setattr(plugin_auto_update, "_spawn_plugin_update_task", spawn)

    def app_for(
        lock_service: _RouteLockService,
        http_resource: _RouteHTTP,
        ssh_pool: object,
    ) -> FastAPI:
        app = FastAPI()
        app.state.container = SimpleNamespace(
            services={MAINTENANCE_LOCK_SERVICE_KEY: lock_service},
            http=http_resource,
            ssh_pool=ssh_pool,
        )
        app.include_router(plugin_auto_update.router)
        database = SimpleNamespace(commit=AsyncMock())
        app.dependency_overrides[get_db] = lambda: database
        app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
            id=7,
            is_admin=False,
        )
        return app

    first_app = app_for(first_lock, first_http, first_pool)
    second_app = app_for(second_lock, second_http, second_pool)
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=first_app),
            base_url="http://first",
        ) as first_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=second_app),
            base_url="http://second",
        ) as second_client,
    ):
        first_response = await first_client.post("/api/servers/31/plugin-auto-update/run")
        second_response = await second_client.post("/api/servers/42/plugin-auto-update/run")

    assert first_response.status_code == second_response.status_code == 202
    assert first_lock.status_calls == [31]
    assert second_lock.status_calls == [42]
    assert [(server_id, http, lock) for server_id, http, _factory, lock in runtime_calls] == [
        (31, first_http, first_lock),
        (42, second_http, second_lock),
    ]
    first_manager = runtime_calls[0][2]()
    second_manager = runtime_calls[1][2]()
    assert first_manager.connection_pool is first_pool
    assert second_manager.connection_pool is second_pool
    assert first_manager.http_resource is first_http
    assert second_manager.http_resource is second_http
