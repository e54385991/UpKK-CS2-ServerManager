from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Request

from api.application import create_app
from api.dependencies import get_ssh_manager
from api.routes.servers import configuration
from modules import AuthType, Server, get_current_active_user, get_db
from services.ssh_manager import SSHManager


class _Session:
    def __init__(self) -> None:
        self.commit_count = 0
        self.transaction_open = False

    async def commit(self) -> None:
        self.commit_count += 1
        self.transaction_open = False


class _DatabaseDependency:
    def __init__(self) -> None:
        self.sessions: list[_Session] = []

    async def __call__(self):
        session = _Session()
        self.sessions.append(session)
        yield session


class _Pool:
    def __init__(self, name: str) -> None:
        self.name = name


class _HTTP:
    def __init__(self, name: str, *, closed: bool = False) -> None:
        self.name = name
        self.is_closed = closed
        self.downloads: list[tuple[str, str, int]] = []

    async def get(self, *_args, **_kwargs):
        return True, {}, None

    async def post(self, *_args, **_kwargs):
        return True, {}, None

    @asynccontextmanager
    async def borrow_client(self):
        yield object()

    async def download_file(
        self,
        url: str,
        local_path: str,
        *,
        timeout: int,
        **_kwargs,
    ):
        self.downloads.append((url, local_path, timeout))
        return False, "intentional test stop"


_NOT_PROVIDED = object()


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


def _app(
    pool: object,
    database: _DatabaseDependency,
    http_resource: object = _NOT_PROVIDED,
):
    resources = {"ssh_pool": pool}
    if http_resource is not _NOT_PROVIDED:
        resources["http"] = http_resource
    app = create_app(
        lifespan=None,
        resource_overrides=resources,
    )

    async def active_user():
        return SimpleNamespace(id=3, is_admin=False)

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_active_user] = active_user
    return app


@pytest.mark.asyncio
async def test_two_create_app_instances_inject_only_their_own_ssh_pool(
    monkeypatch,
) -> None:
    server = _server()
    observed: list[tuple[str, str, int]] = []

    async def authorize(server_id, current_user, db):
        assert server_id == server.id
        assert current_user.id == server.user_id
        await db.commit()
        return server

    async def execute_and_log(db, _server, _target, _commands, ssh_manager, **_kwargs):
        assert db.commit_count >= 1
        assert db.transaction_open is False
        observed.append(
            (
                ssh_manager.connection_pool.name,
                ssh_manager.http_resource.name,
                db.commit_count,
            )
        )
        return {
            "success": True,
            "message": "executed",
            "target": "host",
            "results": [],
        }

    monkeypatch.setattr(configuration, "get_server_with_permission", authorize)
    monkeypatch.setattr(
        configuration,
        "execute_and_log_custom_commands",
        execute_and_log,
    )

    first_database = _DatabaseDependency()
    second_database = _DatabaseDependency()
    first_app = _app(_Pool("first"), first_database, _HTTP("first-http"))
    second_app = _app(_Pool("second"), second_database, _HTTP("second-http"))

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
        first_response = await first_client.post(
            "/servers/17/custom-commands/execute",
            json={"target": "host", "commands": "uptime"},
        )
        second_response = await second_client.post(
            "/servers/17/custom-commands/execute",
            json={"target": "host", "commands": "uptime"},
        )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    expected_body = {
        "success": True,
        "message": "executed",
        "data": {
            "success": True,
            "message": "executed",
            "target": "host",
            "results": [],
        },
    }
    assert first_response.json() == expected_body
    assert second_response.json() == expected_body
    assert observed == [
        ("first", "first-http", 1),
        ("second", "second-http", 1),
    ]


@pytest.mark.asyncio
async def test_custom_command_route_fails_closed_without_application_ssh_pool(
    monkeypatch,
) -> None:
    remote_operation = AsyncMock()
    monkeypatch.setattr(
        configuration,
        "execute_and_log_custom_commands",
        remote_operation,
    )
    database = _DatabaseDependency()
    app = _app(None, database)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/servers/17/custom-commands/execute",
            json={"target": "host", "commands": "uptime"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "SSH connection pool is unavailable"}
    remote_operation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("http_resource", "expected_detail"),
    [
        (None, "Outbound HTTP client is unavailable"),
        (_HTTP("closed", closed=True), "Outbound HTTP client is unavailable"),
        (
            SimpleNamespace(get=lambda: None, post=lambda: None, borrow_client=lambda: None),
            "Outbound HTTP client is unavailable",
        ),
    ],
)
async def test_custom_command_route_fails_closed_without_usable_application_http(
    monkeypatch,
    http_resource,
    expected_detail,
) -> None:
    remote_operation = AsyncMock()
    monkeypatch.setattr(
        configuration,
        "execute_and_log_custom_commands",
        remote_operation,
    )
    database = _DatabaseDependency()
    app = _app(_Pool("application"), database, http_resource)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/servers/17/custom-commands/execute",
            json={"target": "host", "commands": "uptime"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": expected_detail}
    remote_operation.assert_not_awaited()


def test_custom_command_routes_declare_ssh_resource_failure_envelope() -> None:
    paths = create_app(lifespan=None).openapi()["paths"]

    for path in (
        "/servers/{server_id}/custom-commands/execute",
        "/servers/{server_id}/custom-commands/{command_id}/execute",
    ):
        error_schema = paths[path]["post"]["responses"]["503"]["content"]["application/json"][
            "schema"
        ]
        assert error_schema == {"$ref": "#/components/schemas/ErrorResponse"}


@pytest.mark.asyncio
async def test_saved_custom_command_closes_query_transaction_before_remote_io(
    monkeypatch,
) -> None:
    server = _server()
    database = _DatabaseDependency()
    pool = _Pool("application")

    async def authorize(_server_id, _current_user, db):
        await db.commit()
        return server

    async def load_command(db, _server_id, _command_id, _current_user):
        db.transaction_open = True
        return SimpleNamespace(name="diagnostics", target="host", commands="uptime")

    async def execute_and_log(db, _server, _target, _commands, ssh_manager, **_kwargs):
        assert db.commit_count == 2
        assert db.transaction_open is False
        assert ssh_manager.connection_pool is pool
        return {
            "success": True,
            "message": "executed",
            "target": "host",
            "results": [],
        }

    monkeypatch.setattr(configuration, "get_server_with_permission", authorize)
    monkeypatch.setattr(configuration, "get_custom_command_or_404", load_command)
    monkeypatch.setattr(
        configuration,
        "execute_and_log_custom_commands",
        execute_and_log,
    )
    app = _app(pool, database)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/servers/17/custom-commands/9/execute")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "message": "executed",
        "data": {
            "success": True,
            "message": "executed",
            "target": "host",
            "results": [],
        },
    }


@pytest.mark.asyncio
async def test_injected_manager_uses_explicit_pool_for_connection_lease(
    monkeypatch,
) -> None:
    connection = object()
    lease = SimpleNamespace(connection=connection, release=AsyncMock())
    pool = SimpleNamespace(acquire_lease=AsyncMock(return_value=(True, lease, "connected")))
    manager = SSHManager(connection_pool=pool)
    server = _server()
    monkeypatch.setattr("services.ssh.connection._schedule_status_update", lambda *_args: None)

    success, message = await manager.connect(server)

    assert success is True
    assert message == "connected"
    assert manager.conn is connection
    pool.acquire_lease.assert_awaited_once_with(server)

    await manager.disconnect()
    lease.release.assert_awaited_once_with()


def test_legacy_factory_container_keeps_process_global_ssh_pool() -> None:
    from modules.http_helper import http_helper
    from services.ssh_connection_pool import ssh_connection_pool

    app = create_app(lifespan=None)
    manager = get_ssh_manager(Request({"type": "http", "app": app}))

    assert app.state.container.ssh_pool is ssh_connection_pool
    assert manager.connection_pool is ssh_connection_pool
    assert app.state.container.http is http_helper
    assert manager.http_resource is http_helper


def test_no_argument_manager_keeps_non_asgi_global_resource_compatibility() -> None:
    from modules.http_helper import http_helper
    from services.ssh_connection_pool import ssh_connection_pool

    manager = SSHManager()

    assert manager.connection_pool is ssh_connection_pool
    assert manager.http_resource is http_helper


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "expected_timeout"),
    [
        ("deploy_cs2_server", 600),
        ("install_metamod", 180),
        ("install_counterstrikesharp", 300),
        ("install_cs2fixes", 300),
        ("install_swiftly", 300),
    ],
)
async def test_panel_proxy_downloads_use_only_the_injected_http_adapter(
    monkeypatch,
    method_name: str,
    expected_timeout: int,
) -> None:
    from modules.http_helper import http_helper

    global_download = AsyncMock(side_effect=AssertionError("global HTTP fallback used"))
    monkeypatch.setattr(http_helper, "download_file", global_download)

    adapter = _HTTP("application")
    manager = SSHManager(
        connection_pool=_Pool("application"),
        http_resource=adapter,
    )
    server = _server()
    server.game_directory = "/srv/cs2"
    server.use_panel_proxy = True

    async def execute(command: str, **_kwargs):
        if "releases/latest" in command:
            return True, "https://downloads.example/plugin.zip", ""
        return True, "exists", ""

    monkeypatch.setattr(manager, "connect", AsyncMock(return_value=(True, "connected")))
    monkeypatch.setattr(manager, "disconnect", AsyncMock())
    monkeypatch.setattr(manager, "execute_command", execute)
    monkeypatch.setattr(
        manager,
        "_fetch_latest_metamod_url",
        AsyncMock(return_value=(True, "https://downloads.example/metamod.tar.gz")),
    )
    monkeypatch.setattr(
        manager,
        "_fetch_github_release_url",
        AsyncMock(return_value=(True, "https://downloads.example/cs2fixes.tar.gz")),
    )

    success, message = await getattr(manager, method_name)(server)

    assert success is False
    assert "intentional test stop" in message
    assert len(adapter.downloads) == 1
    assert adapter.downloads[0][2] == expected_timeout
    global_download.assert_not_awaited()
