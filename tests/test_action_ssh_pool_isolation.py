from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from api.routes.actions import status as status_routes
from modules import AuthType, Server, get_current_active_user, get_db


def _server() -> Server:
    return Server(
        id=17,
        user_id=3,
        name="isolated-server",
        host="server.example",
        ssh_port=2222,
        ssh_user="cs2",
        auth_type=AuthType.PASSWORD,
        ssh_password="ssh-secret",
        credential_revision=4,
        ssh_host_key_algorithm="ssh-ed25519",
        ssh_host_key_fingerprint="SHA256:confirmed",
    )


class _Session:
    def __init__(self):
        self.commit_count = 0
        self.statements = []

    async def commit(self):
        self.commit_count += 1

    async def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace()


class _DatabaseDependency:
    def __init__(self):
        self.sessions = []

    async def __call__(self):
        session = _Session()
        self.sessions.append(session)
        yield session


class _Pool:
    def __init__(self, name: str, database: _DatabaseDependency):
        self.name = name
        self.database = database
        self.calls = []

    def _record(self, operation, server):
        session = self.database.sessions[-1]
        assert session.commit_count >= 1
        assert isinstance(server, status_routes.SSHServerSnapshot)
        assert server.ssh_password == "ssh-secret"
        self.calls.append((operation, server))

    async def get_connection_info(self, server):
        self._record("info", server)
        return {
            "connected": True,
            "created_at": 10.0,
            "last_used": 12.0,
            "connection_age": 5.0,
            "idle_time": 3.0,
            "in_use": False,
            "reconnection_count": 1,
            "max_reconnections": 5,
            "pooling_enabled": True,
            "connection_key": f"{self.name}:{server.id}",
        }

    async def manual_reconnect(self, server):
        self._record("reconnect", server)
        return True, object(), f"reconnected via {self.name}"

    async def reset_reconnection_counter(self, server):
        self._record("reset", server)
        return True, "reset"


def _app(pool, database):
    app = FastAPI()
    app.state.container = SimpleNamespace(ssh_pool=pool)

    async def active_user():
        return SimpleNamespace(id=3, is_admin=False)

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_active_user] = active_user
    app.include_router(status_routes.router)
    return app


@pytest.fixture
def authorized_server(monkeypatch):
    server = _server()

    async def authorize(db, server_id, current_user):
        assert server_id == server.id
        assert current_user.id == server.user_id
        await db.commit()
        return server

    monkeypatch.setattr(status_routes, "get_server_and_verify_ownership", authorize)
    return server


@pytest.mark.asyncio
async def test_all_ssh_pool_routes_release_db_and_keep_success_shapes(authorized_server):
    database = _DatabaseDependency()
    pool = _Pool("application-pool", database)
    app = _app(pool, database)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        info = await client.get("/servers/17/ssh-connection-info")
        reconnect = await client.post("/servers/17/reconnect-ssh")
        reset = await client.post("/servers/17/reset-reconnect-counter")

    assert info.status_code == 200
    assert info.json() == {
        "connected": True,
        "created_at": 10.0,
        "last_used": 12.0,
        "connection_age": 5.0,
        "idle_time": 3.0,
        "in_use": False,
        "reconnection_count": 1,
        "max_reconnections": 5,
        "pooling_enabled": True,
        "connection_key": "application-pool:17",
    }
    assert reconnect.status_code == 200
    assert reconnect.json() == {
        "success": True,
        "message": "reconnected via application-pool",
    }
    assert reset.status_code == 200
    assert reset.json() == {
        "success": True,
        "message": "重连计数已重置 | Reconnection counter reset",
    }
    assert [operation for operation, _snapshot in pool.calls] == [
        "info",
        "reconnect",
        "reset",
    ]
    assert all(snapshot is not authorized_server for _operation, snapshot in pool.calls)
    assert database.sessions[1].commit_count == 2
    assert len(database.sessions[1].statements) == 1


@pytest.mark.asyncio
async def test_two_apps_use_only_their_own_ssh_pool(authorized_server):
    first_database = _DatabaseDependency()
    second_database = _DatabaseDependency()
    first_pool = _Pool("first", first_database)
    second_pool = _Pool("second", second_database)
    first_app = _app(first_pool, first_database)
    second_app = _app(second_pool, second_database)

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
        first_response = await first_client.get("/servers/17/ssh-connection-info")
        second_response = await second_client.get("/servers/17/ssh-connection-info")

    assert first_response.json()["connection_key"] == "first:17"
    assert second_response.json()["connection_key"] == "second:17"
    assert [operation for operation, _snapshot in first_pool.calls] == ["info"]
    assert [operation for operation, _snapshot in second_pool.calls] == ["info"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("GET", "/servers/17/ssh-connection-info"),
        ("POST", "/servers/17/reconnect-ssh"),
        ("POST", "/servers/17/reset-reconnect-counter"),
    ),
)
async def test_ssh_pool_routes_fail_closed_when_app_resource_is_missing(
    authorized_server,
    method,
    path,
):
    authorized_server.is_ssh_down = True
    database = _DatabaseDependency()
    app = _app(None, database)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.request(method, path)

    assert response.status_code == 503
    assert response.json() == {"detail": "SSH connection pool is unavailable"}
    assert database.sessions[0].commit_count == 1
    assert database.sessions[0].statements == []


def test_legacy_ssh_pool_paths_and_methods_are_unchanged():
    routes = {route.path: route for route in status_routes.router.routes}

    assert routes["/servers/{server_id}/ssh-connection-info"].methods == {"GET"}
    assert routes["/servers/{server_id}/reconnect-ssh"].methods == {"POST"}
    assert routes["/servers/{server_id}/reset-reconnect-counter"].methods == {"POST"}
