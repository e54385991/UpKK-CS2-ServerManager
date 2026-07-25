"""Principal coverage for routes which do not need an ORM user."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, status
from fastapi.routing import APIRoute

from api.dependencies import get_admin_principal
from api.routes import plugin_market, server_status, setup
from cs2_manager.core import Principal
from modules.auth import get_current_principal


def _dependency_calls(endpoint: Callable[..., object]) -> set[object]:
    for router in (setup.router, plugin_market.router, server_status.router):
        for route in router.routes:
            if isinstance(route, APIRoute) and route.endpoint is endpoint:
                return {dependency.call for dependency in route.dependant.dependencies}
    raise AssertionError(f"Route for {endpoint.__name__} was not registered")


def _principal(user_id: int = 7, *, is_admin: bool = False) -> Principal:
    return Principal(
        id=user_id,
        username=f"user-{user_id}",
        email=f"user-{user_id}@example.com",
        is_admin=is_admin,
    )


def test_selected_routes_use_detached_principal_dependencies() -> None:
    for endpoint in (
        setup.list_initialized_servers,
        setup.get_initialized_server,
        setup.delete_initialized_server,
        plugin_market.list_categories,
    ):
        calls = _dependency_calls(endpoint)
        assert get_current_principal in calls
        assert setup.get_current_active_user not in calls

    pool_calls = _dependency_calls(server_status.get_ssh_pool_stats)
    assert get_admin_principal in pool_calls
    assert server_status.get_db not in pool_calls


def test_selected_routes_keep_oauth2_openapi_security() -> None:
    app = FastAPI()
    app.include_router(setup.router)
    app.include_router(plugin_market.router)
    app.include_router(server_status.router)
    paths = app.openapi()["paths"]

    operations = (
        paths["/api/setup/initialized-servers"]["get"],
        paths["/api/setup/initialized-servers/{server_key}"]["get"],
        paths["/api/setup/initialized-servers/{server_key}"]["delete"],
        paths["/api/plugin-market/categories"]["get"],
        paths["/api/server-status/pool/stats"]["get"],
    )
    for operation in operations:
        assert operation["security"] == [{"OAuth2PasswordBearer": []}]


@pytest.mark.asyncio
async def test_initialized_server_routes_keep_owner_checks_and_bodies(monkeypatch) -> None:
    server_data = {
        "user_id": 7,
        "name": "redis-server",
        "host": "server.example.com",
        "ssh_port": 22,
        "ssh_user": "cs2",
        "ssh_password": "legacy-secret",
        "game_directory": "/home/cs2/serverfiles",
        "created_at": 123.0,
    }
    list_servers = AsyncMock(return_value=[{"key": "setup:7:server", **server_data}])
    get_server = AsyncMock(return_value=server_data)
    delete_server = AsyncMock(return_value=True)
    monkeypatch.setattr(setup.redis_manager, "get_initialized_servers", list_servers)
    monkeypatch.setattr(setup.redis_manager, "get_initialized_server", get_server)
    monkeypatch.setattr(setup.redis_manager, "delete_initialized_server", delete_server)

    app = FastAPI()
    app.include_router(setup.router)
    app.dependency_overrides[get_current_principal] = lambda: _principal()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        list_response = await client.get("/api/setup/initialized-servers")
        detail_response = await client.get("/api/setup/initialized-servers/setup:7:server")
        delete_response = await client.delete("/api/setup/initialized-servers/setup:7:server")

        app.dependency_overrides[get_current_principal] = lambda: _principal(8)
        forbidden_response = await client.get("/api/setup/initialized-servers/setup:7:server")

    assert list_response.status_code == status.HTTP_200_OK
    assert list_response.json() == [
        {
            "key": "setup:7:server",
            "name": "redis-server",
            "host": "server.example.com",
            "ssh_port": 22,
            "ssh_user": "cs2",
            "game_directory": "/home/cs2/serverfiles",
            "created_at": 123.0,
        }
    ]
    assert detail_response.status_code == status.HTTP_200_OK
    assert detail_response.json() == server_data
    assert delete_response.status_code == status.HTTP_200_OK
    assert delete_response.json() == {
        "success": True,
        "message": "Initialized server deleted successfully",
    }
    assert forbidden_response.status_code == status.HTTP_403_FORBIDDEN
    list_servers.assert_awaited_once_with(7)
    delete_server.assert_awaited_once_with(7, "setup:7:server")


@pytest.mark.asyncio
async def test_categories_remain_authenticated_and_body_is_unchanged() -> None:
    app = FastAPI()
    app.include_router(plugin_market.router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        unauthenticated = await client.get("/api/plugin-market/categories")

        app.dependency_overrides[get_current_principal] = lambda: _principal()
        authenticated = await client.get("/api/plugin-market/categories")

    assert unauthenticated.status_code == status.HTTP_401_UNAUTHORIZED
    assert authenticated.status_code == status.HTTP_200_OK
    assert authenticated.json() == {
        "success": True,
        "categories": [
            {
                "value": category.value,
                "name": category.value.replace("_", " ").title(),
            }
            for category in plugin_market.PluginCategory
        ],
    }
