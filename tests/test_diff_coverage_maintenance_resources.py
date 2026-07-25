"""Focused diff coverage for maintenance and plugin resource boundaries."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from api.routes import plugin_auto_update
from api.routes.servers import maintenance
from modules import AuthType, GitHubPluginInstallRequest, Server
from services import plugin_installation


class _HTTP:
    is_closed = False

    async def get(self, _url: str, **_kwargs: Any):
        return True, {}, None

    async def post(self, _url: str, **_kwargs: Any):
        return True, {}, None

    @asynccontextmanager
    async def borrow_client(self):
        yield SimpleNamespace()


def _server(server_id: int = 17, *, user_id: int = 7) -> Server:
    return Server(
        id=server_id,
        user_id=user_id,
        name=f"server-{server_id}",
        host="server.example",
        ssh_port=22,
        ssh_user="cs2",
        auth_type=AuthType.PASSWORD,
        ssh_password="secret",
        game_directory=f"/srv/cs2-{server_id}",
        ssh_health_status="completely_down",
        consecutive_ssh_failures=9,
        is_ssh_down=True,
    )


class _DiskCache:
    def __init__(self) -> None:
        self.get_calls = 0
        self.set_calls = 0

    async def get(self, _key: str) -> object:
        self.get_calls += 1
        raise RuntimeError("cache read unavailable")

    async def set(self, _key: str, _value: object, *, expire: int) -> bool:
        assert expire == 3600
        self.set_calls += 1
        raise RuntimeError("cache write unavailable")


class _Concurrency:
    def __init__(self) -> None:
        self.active = 0
        self.maximum = 0
        self.targets: list[Server] = []


class _DiskManager:
    def __init__(self, tracker: _Concurrency) -> None:
        self.tracker = tracker
        self.command_count = 0
        self.connected = False

    async def connect(self, target: Server) -> tuple[bool, str]:
        self.tracker.targets.append(target)
        self.tracker.active += 1
        self.tracker.maximum = max(self.tracker.maximum, self.tracker.active)
        self.connected = True
        await asyncio.sleep(0.01)
        return target.id != 10, "connected"

    async def execute_command(
        self,
        _command: str,
        timeout: int = 30,
    ) -> tuple[bool, str, str]:
        self.command_count += 1
        if self.command_count == 1:
            assert timeout == 60
            return True, str(1024**3), ""
        return True, "/dev/sda 10G 1G 8G 10% /srv", ""

    async def disconnect(self) -> None:
        if self.connected:
            self.connected = False
            self.tracker.active -= 1


class _DiskDatabase:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1
        self.events.append("db_commit")


@pytest.mark.asyncio
async def test_batch_disk_space_bounds_concurrency_and_tolerates_cache_failures(
    monkeypatch,
) -> None:
    records = [_server(server_id) for server_id in range(1, 11)]
    events: list[str] = []
    database = _DiskDatabase(events)
    cache = _DiskCache()
    tracker = _Concurrency()
    pool = object()
    http = _HTTP()

    async def get_all_by_user(_cls, db, user_id):
        assert db is database
        assert user_id == 7
        return records

    def manager_factory(*, connection_pool, http_resource):
        assert connection_pool is pool
        assert http_resource is http
        return _DiskManager(tracker)

    monkeypatch.setattr(
        maintenance.Server,
        "get_all_by_user",
        classmethod(get_all_by_user),
    )
    monkeypatch.setattr(maintenance, "SSHManager", manager_factory)

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(container=SimpleNamespace(redis=cache)))
    )
    response = await maintenance.get_all_servers_disk_space(
        request=request,  # type: ignore[arg-type]
        force_refresh=False,
        db=database,  # type: ignore[arg-type]
        current_user=SimpleNamespace(id=7),  # type: ignore[arg-type]
        ssh_manager=SimpleNamespace(
            connection_pool=pool,
            http_resource=http,
        ),  # type: ignore[arg-type]
    )

    assert database.commits == 1
    assert events == ["db_commit"]
    assert tracker.maximum == 4
    assert tracker.active == 0
    assert len(tracker.targets) == 10
    assert all(
        snapshot is not record for snapshot, record in zip(tracker.targets, records, strict=True)
    )
    assert cache.get_calls == 10
    assert cache.set_calls == 9
    assert response["servers"]["1"] == {
        "used_gb": 1.0,
        "total_gb": 10.0,
        "available_gb": 8.0,
        "used_percent": 10.0,
    }
    assert response["servers"]["10"] is None


class _ReconnectDatabase:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.commits = 0

    async def get(self, model, server_id: int) -> Server:
        assert model is Server
        assert server_id == self.server.id
        return self.server

    async def commit(self) -> None:
        self.commits += 1


class _ReconnectManager:
    def __init__(
        self,
        database: _ReconnectDatabase,
        *,
        success: bool,
        error: Exception | None = None,
    ) -> None:
        self.database = database
        self.success = success
        self.error = error
        self.target: Server | None = None
        self.disconnects = 0

    async def connect(self, target: Server) -> tuple[bool, str]:
        assert self.database.commits == 1
        self.target = target
        if self.error is not None:
            raise self.error
        return self.success, "result"

    async def disconnect(self) -> None:
        self.disconnects += 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ("success", "failure", "exception"))
async def test_manual_reconnect_detaches_and_maps_all_remote_outcomes(mode: str) -> None:
    server = _server()
    database = _ReconnectDatabase(server)
    manager = _ReconnectManager(
        database,
        success=mode == "success",
        error=RuntimeError("host key mismatch") if mode == "exception" else None,
    )

    response = await maintenance.manual_ssh_reconnect(
        server_id=17,
        db=database,  # type: ignore[arg-type]
        current_user=SimpleNamespace(id=7),  # type: ignore[arg-type]
        ssh_manager=manager,  # type: ignore[arg-type]
    )

    assert manager.target is not server
    assert manager.target is not None
    assert manager.target.ssh_password == "secret"
    assert manager.disconnects == 1
    if mode == "success":
        assert response == {
            "success": True,
            "message": "SSH connection successful - server health restored",
            "ssh_health_status": "healthy",
        }
        assert database.commits == 2
        assert server.last_ssh_success is not None
        assert server.last_ssh_health_check == server.last_ssh_success
        assert server.consecutive_ssh_failures == 0
        assert server.is_ssh_down is False
        assert server.ssh_health_status == "healthy"
    else:
        assert response == {
            "success": False,
            "message": "SSH connection failed - server is still unreachable",
            "ssh_health_status": "completely_down",
        }
        assert database.commits == 1
        assert server.consecutive_ssh_failures == 9
        assert server.is_ssh_down is True


def test_plugin_update_resources_keep_direct_compatibility_and_fail_closed() -> None:
    assert plugin_auto_update._plugin_update_resources(plugin_auto_update._DIRECT_SSH_MANAGER) == (
        None,
        None,
    )

    with pytest.raises(HTTPException) as missing_pool:
        plugin_auto_update._plugin_update_resources(
            SimpleNamespace(connection_pool=None, http_resource=_HTTP())
        )
    assert missing_pool.value.status_code == 503
    assert missing_pool.value.detail == "SSH connection pool is unavailable"

    with pytest.raises(HTTPException) as missing_http:
        plugin_auto_update._plugin_update_resources(
            SimpleNamespace(connection_pool=object(), http_resource=None)
        )
    assert missing_http.value.status_code == 503
    assert missing_http.value.detail == "Outbound HTTP client is unavailable"

    pool = object()
    http = _HTTP()
    resource, factory = plugin_auto_update._plugin_update_resources(
        SimpleNamespace(connection_pool=pool, http_resource=http)
    )
    assert resource is http
    assert factory is not None
    manager = factory()
    assert manager.connection_pool is pool
    assert manager.http_resource is http


@pytest.mark.asyncio
async def test_plugin_test_update_forwards_detached_resources_to_background(
    monkeypatch,
) -> None:
    database = SimpleNamespace(commit=AsyncMock())
    locks = SimpleNamespace(is_locked=AsyncMock(return_value=False), get=lambda: None)
    pool = object()
    http = _HTTP()
    observed: dict[str, object] = {}

    monkeypatch.setattr(plugin_auto_update, "owned_server", AsyncMock())
    monkeypatch.setattr(plugin_auto_update, "owned_plugin", AsyncMock())

    def check_plugin(server_id, plugin_id, **kwargs):
        observed.update(server_id=server_id, plugin_id=plugin_id, **kwargs)

        async def pending() -> None:
            return None

        return pending()

    def spawn(_request, coroutine, *, name: str):
        observed["task_name"] = name
        coroutine.close()
        return None

    monkeypatch.setattr(
        plugin_auto_update.plugin_auto_update_service,
        "check_plugin",
        check_plugin,
    )
    monkeypatch.setattr(plugin_auto_update, "_spawn_plugin_update_task", spawn)

    response = await plugin_auto_update.test_plugin_update(
        server_id=17,
        plugin_id=9,
        request=SimpleNamespace(),  # type: ignore[arg-type]
        db=database,  # type: ignore[arg-type]
        current_user=SimpleNamespace(id=7),  # type: ignore[arg-type]
        lock_service=locks,  # type: ignore[arg-type]
        ssh_manager=SimpleNamespace(
            connection_pool=pool,
            http_resource=http,
        ),  # type: ignore[arg-type]
    )

    database.commit.assert_awaited_once_with()
    assert response.success is True
    assert observed["server_id"] == 17
    assert observed["plugin_id"] == 9
    assert observed["http_resource"] is http
    assert observed["lock_service"] is locks
    assert observed["task_name"] == "plugin-update-test-17-9"
    factory = observed["ssh_manager_factory"]
    assert callable(factory)
    manager = factory()
    assert manager.connection_pool is pool
    assert manager.http_resource is http


@pytest.mark.asyncio
async def test_plugin_server_lookup_rejects_identity_without_id() -> None:
    with pytest.raises(LookupError, match="Server not found"):
        await plugin_installation.get_server_for_user(
            SimpleNamespace(),  # type: ignore[arg-type]
            17,
            SimpleNamespace(id=None, is_admin=False),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_direct_plugin_install_builds_compat_resources_and_notification(
    monkeypatch,
) -> None:
    server = _server()
    user = SimpleNamespace(id=7, is_admin=False)
    manager = SimpleNamespace(
        connect=AsyncMock(return_value=(False, "offline")),
        disconnect=AsyncMock(),
    )
    queue_notify = Mock()
    monkeypatch.setattr(
        plugin_installation,
        "get_server_for_user",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(plugin_installation, "SSHManager", lambda: manager)
    monkeypatch.setattr(plugin_installation, "send_deployment_update", AsyncMock())
    monkeypatch.setattr(
        plugin_installation.discord_notification_service,
        "queue_notify",
        queue_notify,
    )
    request = GitHubPluginInstallRequest(
        download_url="https://github.com/acme/plugin/releases/download/v1/plugin.zip",
        record_installation=False,
    )

    response = await plugin_installation.install_github_plugin(
        17,
        request,
        SimpleNamespace(),  # type: ignore[arg-type]
        user,  # type: ignore[arg-type]
    )

    assert response.success is False
    assert response.message == "SSH connection failed: offline"
    manager.disconnect.assert_awaited_once_with()
    queue_notify.assert_called_once()
    assert queue_notify.call_args.kwargs["details"] == {"Download URL": request.download_url}
