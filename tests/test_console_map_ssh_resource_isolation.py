from __future__ import annotations

import asyncio
import warnings
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException, WebSocket

from api import dependencies
from api.dependencies import SSHManagerProvider
from api.routes import map_management
from api.routes.actions import console as console_routes
from modules import get_current_active_user, get_db


class _HTTP:
    def __init__(self, name: str) -> None:
        self.name = name

    async def get(self, *_args: Any, **_kwargs: Any):
        return True, {}, None

    async def post(self, *_args: Any, **_kwargs: Any):
        return True, {}, None

    @asynccontextmanager
    async def borrow_client(self):
        yield object()

    async def download_file(self, *_args: Any, **_kwargs: Any):
        return False, "not used"


class _Manager:
    instances: list[_Manager] = []

    def __init__(self, *, connection_pool: object, http_resource: object) -> None:
        self.connection_pool = connection_pool
        self.http_resource = http_resource
        self.conn = None
        self.disconnect_count = 0
        self.__class__.instances.append(self)

    async def connect(self, _server: object):
        return True, "connected"

    async def disconnect(self) -> None:
        self.disconnect_count += 1


class _WebSocket:
    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.accepted = False
        self.messages: list[dict[str, object]] = []
        self.close_calls: list[tuple[int, str]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict[str, object]) -> None:
        self.messages.append(message)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_calls.append((code, reason))


def _app(pool: object | None, http_resource: object | None) -> FastAPI:
    app = FastAPI()
    app.state.container = SimpleNamespace(ssh_pool=pool, http=http_resource)
    database = SimpleNamespace(commit=AsyncMock())
    app.state.test_database = database

    async def active_user():
        return SimpleNamespace(id=7, is_admin=False)

    async def get_database():
        yield database

    app.dependency_overrides[get_db] = get_database
    app.dependency_overrides[get_current_active_user] = active_user
    app.include_router(map_management.router)
    return app


def test_ssh_provider_resolves_current_app_for_http_and_websocket_scopes(
    monkeypatch,
) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient

    pool = SimpleNamespace(name="scope-pool")
    http_resource = _HTTP("scope-http")
    app = FastAPI()
    app.state.container = SimpleNamespace(ssh_pool=pool, http=http_resource)
    _Manager.instances = []
    monkeypatch.setattr(dependencies, "SSHManager", _Manager)

    @app.get("/http-probe")
    async def http_probe(ssh_manager: SSHManagerProvider):
        return {
            "pool": ssh_manager.connection_pool.name,
            "http": ssh_manager.http_resource.name,
        }

    @app.websocket("/websocket-probe")
    async def websocket_probe(websocket: WebSocket, ssh_manager: SSHManagerProvider):
        await websocket.accept()
        await websocket.send_json(
            {
                "pool": ssh_manager.connection_pool.name,
                "http": ssh_manager.http_resource.name,
            }
        )
        await websocket.close()

    with TestClient(app) as client:
        assert client.get("/http-probe").json() == {
            "pool": "scope-pool",
            "http": "scope-http",
        }
        with client.websocket_connect("/websocket-probe") as websocket:
            assert websocket.receive_json() == {
                "pool": "scope-pool",
                "http": "scope-http",
            }

    assert [(manager.connection_pool, manager.http_resource) for manager in _Manager.instances] == [
        (pool, http_resource),
        (pool, http_resource),
    ]


@pytest.mark.asyncio
async def test_map_connect_preserves_502_when_rejected_connection_cleanup_fails() -> None:
    manager = SimpleNamespace(
        connect=AsyncMock(return_value=(False, "denied")),
        disconnect=AsyncMock(side_effect=RuntimeError("cleanup failed")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await map_management._connect(SimpleNamespace(), manager)

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "SSH connection failed: denied"
    manager.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_map_connect_preserves_connect_exception_when_cleanup_fails() -> None:
    connect_error = RuntimeError("connect failed")
    manager = SimpleNamespace(
        connect=AsyncMock(side_effect=connect_error),
        disconnect=AsyncMock(side_effect=RuntimeError("cleanup failed")),
    )

    with pytest.raises(RuntimeError) as exc_info:
        await map_management._connect(SimpleNamespace(), manager)

    assert exc_info.value is connect_error
    manager.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_map_connect_releases_manager_when_cancelled() -> None:
    manager = SimpleNamespace(
        connect=AsyncMock(side_effect=asyncio.CancelledError()),
        disconnect=AsyncMock(),
    )

    with pytest.raises(asyncio.CancelledError):
        await map_management._connect(SimpleNamespace(), manager)

    manager.disconnect.assert_awaited_once()


@pytest.fixture
def authenticated_console(monkeypatch):
    server = SimpleNamespace(id=12, user_id=7, host="server.example")

    async def authenticate(_websocket, server_id):
        assert server_id == server.id
        return SimpleNamespace(id=7, is_admin=False), server

    monkeypatch.setattr(console_routes, "authenticate_websocket", authenticate)
    return server


@pytest.mark.asyncio
async def test_two_console_apps_construct_ssh_from_only_their_own_resources(
    monkeypatch,
    authenticated_console,
) -> None:
    first_pool = SimpleNamespace(name="first-pool")
    second_pool = SimpleNamespace(name="second-pool")
    first_http = _HTTP("first-http")
    second_http = _HTTP("second-http")
    first_websocket = _WebSocket(_app(first_pool, first_http))
    second_websocket = _WebSocket(_app(second_pool, second_http))
    _Manager.instances = []
    monkeypatch.setattr(dependencies, "SSHManager", _Manager)

    await console_routes.ssh_console_websocket(first_websocket, authenticated_console.id)
    await console_routes.ssh_console_websocket(second_websocket, authenticated_console.id)

    assert [(manager.connection_pool, manager.http_resource) for manager in _Manager.instances] == [
        (first_pool, first_http),
        (second_pool, second_http),
    ]
    assert first_websocket.accepted is True
    assert second_websocket.accepted is True
    assert all(manager.disconnect_count == 1 for manager in _Manager.instances)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    (
        console_routes.ssh_console_websocket,
        console_routes.game_console_websocket,
    ),
)
@pytest.mark.parametrize(
    ("pool", "http_resource", "reason"),
    (
        (None, _HTTP("available"), "SSH connection pool is unavailable"),
        (SimpleNamespace(name="available"), None, "Outbound HTTP client is unavailable"),
    ),
)
async def test_console_websockets_fail_closed_when_app_ssh_resources_are_missing(
    monkeypatch,
    authenticated_console,
    endpoint,
    pool,
    http_resource,
    reason,
) -> None:
    websocket = _WebSocket(_app(pool, http_resource))

    class _UnexpectedManager:
        def __init__(self, **_kwargs):
            raise AssertionError("SSH manager must not use a global fallback")

    monkeypatch.setattr(dependencies, "SSHManager", _UnexpectedManager)

    await endpoint(websocket, authenticated_console.id)

    assert websocket.accepted is False
    assert websocket.messages == []
    assert websocket.close_calls == [(1011, reason)]


@pytest.mark.asyncio
async def test_two_map_apps_inject_only_their_own_ssh_resources(monkeypatch) -> None:
    first_pool = SimpleNamespace(name="first-pool")
    second_pool = SimpleNamespace(name="second-pool")
    first_http = _HTTP("first-http")
    second_http = _HTTP("second-http")
    first_app = _app(first_pool, first_http)
    second_app = _app(second_pool, second_http)
    server = SimpleNamespace(id=12, game_directory="/srv/cs2")
    _Manager.instances = []

    async def authorize(server_id, _current_user, _db):
        assert server_id == server.id
        return server

    async def inspect(manager, _server):
        return {
            "counterstrikesharp_installed": True,
            "mapchooser_installed": True,
            "maps_file_exists": True,
            "plugin_config_file_exists": True,
            "ready": True,
            "plugin_center_name": map_management.PLUGIN_CENTER_NAME,
            "plugin_center_url": map_management.PLUGIN_CENTER_URL,
            "counterstrikesharp_install_action": "install_counterstrikesharp",
            "maps_path": "/srv/cs2/maps.txt",
            "plugin_config_path": "/srv/cs2/config.json",
            "mapchooser_plugin_path": "/srv/cs2/MapChooser",
        }

    monkeypatch.setattr(dependencies, "SSHManager", _Manager)
    monkeypatch.setattr(map_management, "get_server_with_permission", authorize)
    monkeypatch.setattr(map_management, "_inspect_prerequisites", inspect)

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
        first_response = await first_client.get("/servers/12/maps/status")
        second_response = await second_client.get("/servers/12/maps/status")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert [(manager.connection_pool, manager.http_resource) for manager in _Manager.instances] == [
        (first_pool, first_http),
        (second_pool, second_http),
    ]
    first_app.state.test_database.commit.assert_awaited_once()
    second_app.state.test_database.commit.assert_awaited_once()
    assert all(manager.disconnect_count == 1 for manager in _Manager.instances)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pool", "http_resource", "detail"),
    (
        (None, _HTTP("available"), "SSH connection pool is unavailable"),
        (SimpleNamespace(name="available"), None, "Outbound HTTP client is unavailable"),
    ),
)
async def test_map_routes_fail_closed_when_app_ssh_resources_are_missing(
    monkeypatch,
    pool,
    http_resource,
    detail,
) -> None:
    app = _app(pool, http_resource)

    async def unexpected_authorization(*_args, **_kwargs):
        raise AssertionError("resource dependency must fail before route execution")

    monkeypatch.setattr(
        map_management,
        "get_server_with_permission",
        unexpected_authorization,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/servers/12/maps/status")

    assert response.status_code == 503
    assert response.json() == {"detail": detail}
