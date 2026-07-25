"""Remaining SSH and maintenance routes must use application-owned resources."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute

from api.application import register_exception_handlers
from api.dependencies import get_ssh_manager, resolve_maintenance_lock_service
from api.routes import plugin_configs
from api.routes.actions import deployment
from api.routes.servers import maintenance
from modules import get_current_active_user, get_db
from services.maintenance_lock import OperationCoordinationUnavailable
from services.ssh_manager import SSHManager


def _dependency_calls(route: APIRoute) -> set[object]:
    calls: set[object] = set()
    pending = list(route.dependant.dependencies)
    while pending:
        dependency = pending.pop()
        calls.add(dependency.call)
        pending.extend(dependency.dependencies)
    return calls


def _route_for(router, endpoint) -> APIRoute:
    return next(
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.endpoint is endpoint
    )


def _http_resource():
    return SimpleNamespace(
        get=AsyncMock(),
        post=AsyncMock(),
        borrow_client=Mock(),
        download_file=AsyncMock(),
        is_closed=False,
    )


def _app(router, *, ssh_pool: object | None, services: dict[str, object] | None = None):
    app = FastAPI()
    app.state.container = SimpleNamespace(
        ssh_pool=ssh_pool,
        http=_http_resource(),
        services=services or {},
    )
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        id=7,
        is_admin=False,
    )
    return app


def test_remote_routes_declare_the_application_ssh_dependency() -> None:
    endpoints = (
        (
            plugin_configs.router,
            (
                plugin_configs.create_source,
                plugin_configs.browse_source_path,
                plugin_configs.load_source_files,
                plugin_configs.get_config_file,
                plugin_configs.save_config_file,
            ),
        ),
        (
            maintenance.router,
            (
                maintenance.scan_server_cleanup,
                maintenance.delete_server_cleanup_items,
                maintenance.restore_server_s3_backup,
                maintenance.get_server_cpu_count,
                maintenance.get_server_disk_space,
                maintenance.check_server_deployment,
            ),
        ),
        (
            deployment.router,
            (
                deployment.cancel_deployment,
                deployment.server_action,
            ),
        ),
    )

    for router, route_endpoints in endpoints:
        for endpoint in route_endpoints:
            route = _route_for(router, endpoint)
            assert get_ssh_manager in _dependency_calls(route)
            assert "SSHManager()" not in inspect.getsource(endpoint)


def test_destructive_routes_declare_the_application_lock_dependency() -> None:
    endpoints = (
        (plugin_configs.router, plugin_configs.save_config_file),
        (maintenance.router, maintenance.delete_server_cleanup_items),
        (maintenance.router, maintenance.restore_server_s3_backup),
        (deployment.router, deployment.server_action),
    )

    for router, endpoint in endpoints:
        route = _route_for(router, endpoint)
        assert resolve_maintenance_lock_service in _dependency_calls(route)


@pytest.mark.parametrize("module", (plugin_configs, maintenance, deployment))
def test_direct_ssh_facades_only_fall_back_for_the_identity_sentinel(
    monkeypatch,
    module,
) -> None:
    legacy = SimpleNamespace(name="legacy")
    explicit = SimpleNamespace(disconnect=AsyncMock())
    monkeypatch.setattr(module, "SSHManager", lambda: legacy)

    assert module._coerce_ssh_manager(module._DIRECT_SSH_MANAGER) is legacy
    assert module._coerce_ssh_manager(explicit) is explicit
    with pytest.raises(HTTPException) as exc_info:
        module._coerce_ssh_manager(object())
    assert exc_info.value.status_code == 503


def test_action_tracking_factory_only_captures_application_ssh_resources(
    monkeypatch,
) -> None:
    pool = object()
    http_resource = object()
    constructed: list[tuple[object, object]] = []

    def manager_factory(*, connection_pool, http_resource):
        constructed.append((connection_pool, http_resource))
        return SimpleNamespace(
            connection_pool=connection_pool,
            http_resource=http_resource,
        )

    monkeypatch.setattr(deployment, "SSHManager", manager_factory)
    factory = deployment._resource_bound_ssh_manager_factory(
        cast(
            SSHManager,
            SimpleNamespace(
                connection_pool=pool,
                http_resource=http_resource,
            ),
        )
    )

    manager = factory()

    assert manager.connection_pool is pool
    assert manager.http_resource is http_resource
    assert constructed == [(pool, http_resource)]
    assert inspect.getclosurevars(factory).nonlocals == {
        "connection_pool": pool,
        "http_resource": http_resource,
    }
    route_source = inspect.getsource(deployment.server_action)
    assert "http_resource=ssh_manager.http_resource" in route_source
    assert "ssh_manager_factory=tracking_ssh_factory" in route_source


@pytest.mark.parametrize("module", (plugin_configs, maintenance))
def test_direct_lock_facades_reject_invalid_dependency_placeholders(
    monkeypatch,
    module,
) -> None:
    legacy = SimpleNamespace(get=Mock())
    explicit = SimpleNamespace(get=Mock())
    monkeypatch.setattr(module, "maintenance_lock_service", legacy)

    assert module._maintenance_locks(module._DIRECT_MAINTENANCE_LOCK) is legacy
    assert module._maintenance_locks(explicit) is explicit
    with pytest.raises(OperationCoordinationUnavailable):
        module._maintenance_locks(object())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("router", "method", "path"),
    (
        (
            plugin_configs.router,
            "GET",
            "/servers/1/plugin-configs/browse",
        ),
        (
            maintenance.router,
            "GET",
            "/servers/1/cleanup/scan",
        ),
        (
            deployment.router,
            "DELETE",
            "/servers/1/deployment-lock",
        ),
    ),
)
async def test_remote_routes_fail_closed_without_an_application_ssh_pool(
    router,
    method: str,
    path: str,
) -> None:
    app = _app(router, ssh_pool=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.request(method, path)

    assert response.status_code == 503
    assert response.json() == {"detail": "SSH connection pool is unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("router", "method", "path", "body"),
    (
        (
            maintenance.router,
            "POST",
            "/servers/1/cleanup/delete",
            {"mode": "safe", "paths": []},
        ),
        (
            maintenance.router,
            "POST",
            "/servers/1/s3-restore",
            {"object_key": "users/7/servers/1/plugins.tar.gz"},
        ),
        (
            deployment.router,
            "POST",
            "/servers/1/actions",
            {"action": "status"},
        ),
    ),
)
async def test_destructive_routes_fail_closed_without_application_coordination(
    router,
    method: str,
    path: str,
    body: dict[str, object],
) -> None:
    app = _app(router, ssh_pool=object())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.request(method, path, json=body)

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Operation coordination is unavailable; refusing destructive operation"
    }
