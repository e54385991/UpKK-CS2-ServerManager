"""Behavioral coverage for production-hardening branches added by the refactor."""

from __future__ import annotations

import importlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncssh
import pytest
from fastapi import FastAPI, HTTPException, Response
from starlette.requests import Request

from api import lifecycle as lifecycle_module
from api.lifecycle import ApplicationLifecycle
from api.routes import pages
from api.routes.file_manager import common as file_common
from api.routes.servers import crud
from modules.models import AuthType, Server, ServerStatus
from modules.schemas import (
    ServerCreate,
    ServerUpdate,
    SSHHostKeyConfirmRequest,
    SSHHostKeyScanRequest,
)
from services.ssh_host_keys import SSHHostKeyIdentity
from services.ssh_manager import SSHManager


class _AsyncContext:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False


@pytest.mark.asyncio
async def test_application_lifecycle_starts_and_closes_every_owned_resource(monkeypatch):
    events: list[str] = []

    def async_event(name: str):
        async def callback(*_args, **_kwargs):
            events.append(name)

        return callback

    database = SimpleNamespace(
        engine=object(),
        migrate=async_event("database-migrate"),
        initialize=async_event("database-initialize"),
        close=async_event("database-close"),
        session_factory=lambda: _AsyncContext(object()),
    )
    redis = SimpleNamespace(
        delete_by_pattern=AsyncMock(return_value=3),
        close=async_event("redis-close"),
    )
    http = SimpleNamespace(close=async_event("http-close"))
    pool = SimpleNamespace(
        start_cleanup=async_event("pool-start"),
        stop_cleanup=async_event("pool-stop"),
        close_all=async_event("pool-close"),
    )
    supervisor = SimpleNamespace(
        start=Mock(side_effect=lambda: events.append("supervisor-start")),
        shutdown=AsyncMock(side_effect=async_event("supervisor-stop")),
    )
    container = SimpleNamespace(
        database=database,
        redis=redis,
        http=http,
        ssh_pool=pool,
        task_supervisor=supervisor,
    )

    from services.a2s_cache_service import a2s_cache_service
    from services.auto_update_service import auto_update_service
    from services.deployment_progress import flush_deployment_progress
    from services.plugin_auto_update_service import plugin_auto_update_service
    from services.s3_backup_service import s3_backup_service
    from services.scheduled_task_service import scheduled_task_service
    from services.server_monitor import server_monitor
    from services.ssh_health_monitor import ssh_health_monitor
    from services.steam_inf_service import steam_inf_service

    services = (
        (a2s_cache_service, "a2s"),
        (steam_inf_service, "steam-inf"),
        (auto_update_service, "auto-update"),
        (plugin_auto_update_service, "plugin-update"),
        (scheduled_task_service, "scheduled"),
        (ssh_health_monitor, "ssh-health"),
    )
    for service, name in services:
        monkeypatch.setattr(service, "start", AsyncMock(side_effect=async_event(f"{name}-start")))
        monkeypatch.setattr(service, "stop", AsyncMock(side_effect=async_event(f"{name}-stop")))

    monkeypatch.setattr(s3_backup_service, "close", async_event("s3-close"))
    migration_check = AsyncMock()
    monkeypatch.setattr(lifecycle_module, "require_database_current", migration_check)
    monkeypatch.setattr(
        "services.deployment_progress.flush_deployment_progress",
        async_event("progress-flush"),
    )
    assert flush_deployment_progress is not None
    monkeypatch.setattr(
        lifecycle_module.Server,
        "get_all_with_panel_monitoring",
        AsyncMock(return_value=[SimpleNamespace(id=8, name="monitored")]),
    )
    monkeypatch.setattr(
        server_monitor,
        "start_monitoring",
        Mock(side_effect=lambda *_args: events.append("monitor-start")),
    )
    monkeypatch.setattr(server_monitor, "stop_all", async_event("monitor-stop"))
    runtime_cleanup = AsyncMock(side_effect=async_event("runtime-cleanup"))
    monkeypatch.setattr(lifecycle_module, "_cleanup_runtime_tasks", runtime_cleanup)

    lifecycle = ApplicationLifecycle(container=container)
    await lifecycle.start()
    await lifecycle.start()

    assert lifecycle.started is True
    assert lifecycle.cleanup_names == (
        "database engine",
        "HTTP client",
        "Redis client",
        "S3 client cache",
        "deployment progress buffer",
        "SSH connection pool",
        "A2S cache service",
        "steam.inf cache service",
        "auto-update service",
        "plugin auto-update service",
        "scheduled task service",
        "SSH health monitor",
        "server monitor",
    )
    redis.delete_by_pattern.assert_awaited_once_with("a2s:server:*")
    migration_check.assert_awaited_once_with(database.engine)
    assert events.count("a2s-start") == 1
    assert "monitor-start" in events

    await lifecycle.stop()

    assert lifecycle.started is False
    assert lifecycle.cleanup_names == ()
    assert events.index("monitor-stop") < events.index("pool-stop")
    assert events.index("pool-close") < events.index("progress-flush")
    assert events[-3:] == ["redis-close", "http-close", "database-close"]
    supervisor.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifecycle_cleanup_helpers_are_resilient(monkeypatch, caplog):
    from services import ssh_manager as manager_module
    from services.discord_notification_service import discord_notification_service

    pool_module = importlib.import_module("services.ssh_connection_pool")

    monkeypatch.setattr(
        lifecycle_module,
        "shutdown_background_tasks",
        AsyncMock(side_effect=RuntimeError("registry failed")),
    )
    monkeypatch.setattr(discord_notification_service, "shutdown", AsyncMock())
    monkeypatch.setattr(manager_module, "shutdown_background_tasks", AsyncMock())

    await lifecycle_module._cleanup_runtime_tasks()
    assert "Runtime task shutdown failed" in caplog.text

    pool = SimpleNamespace(stop_cleanup=AsyncMock(), close_all=AsyncMock())
    monkeypatch.setattr(pool_module, "ssh_connection_pool", pool)
    await lifecycle_module._close_ssh_pool()
    pool.stop_cleanup.assert_awaited_once()
    pool.close_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifecycle_compatibility_helpers_and_supervisor_failure(monkeypatch, caplog):
    app = FastAPI()
    lifecycle = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    app.state.lifecycle = lifecycle

    await lifecycle_module.start_application(app)
    await lifecycle_module.stop_application(app)
    lifecycle.start.assert_awaited_once()
    lifecycle.stop.assert_awaited_once()

    del app.state.lifecycle
    replacement = SimpleNamespace(start=AsyncMock())
    monkeypatch.setattr(lifecycle_module, "ApplicationLifecycle", Mock(return_value=replacement))
    await lifecycle_module.start_application(app)
    assert app.state.lifecycle is replacement

    import main

    assert lifecycle_module._resolve_app(None) is main.app

    failing_supervisor = SimpleNamespace(
        shutdown=AsyncMock(side_effect=RuntimeError("supervisor failed"))
    )
    owned = ApplicationLifecycle(container=SimpleNamespace(task_supervisor=failing_supervisor))
    monkeypatch.setattr(lifecycle_module, "_cleanup_runtime_tasks", AsyncMock())
    await owned.stop()
    failing_supervisor.shutdown.assert_awaited_once()


def _request_with_settings(registration_enabled: bool) -> Request:
    app = FastAPI()
    app.state.settings = SimpleNamespace(registration_enabled=registration_enabled)
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/register",
            "headers": [],
            "app": app,
        }
    )


@pytest.mark.asyncio
async def test_registration_page_obeys_per_application_setting(monkeypatch):
    render = Mock(return_value="rendered")
    monkeypatch.setattr(pages.templates, "TemplateResponse", render)

    with pytest.raises(HTTPException) as exc_info:
        await pages.register_page(_request_with_settings(False))
    assert exc_info.value.status_code == 404

    request = _request_with_settings(True)
    assert await pages.register_page(request) == "rendered"
    render.assert_called_once_with(request, "register.html")


@pytest.mark.asyncio
async def test_file_editor_page_only_authorizes_and_renders_shell(monkeypatch):
    permission = AsyncMock(return_value=SimpleNamespace(id=4))
    render = Mock(return_value="editor-shell")
    monkeypatch.setattr(pages.servers, "get_server_with_permission", permission)
    monkeypatch.setattr(pages.templates, "TemplateResponse", render)
    request = _request_with_settings(True)

    result = await pages.file_editor_popup(
        request,
        server_id=4,
        file_path="/srv/cs2/server.cfg",
        file_name="server.cfg",
        db=object(),
        current_user=SimpleNamespace(id=7),
    )

    assert result == "editor-shell"
    permission.assert_awaited_once()
    context = render.call_args.args[2]
    assert context == {
        "server_id": 4,
        "file_path": "/srv/cs2/server.cfg",
        "file_name": "server.cfg",
    }


class _CrudDatabase:
    def __init__(self) -> None:
        self.commits = 0
        self.added: list[object] = []

    def add(self, value) -> None:
        self.added.append(value)
        if isinstance(value, Server):
            value.id = 91

    async def commit(self) -> None:
        self.commits += 1

    async def flush(self) -> None:
        return None

    async def refresh(self, value) -> None:
        if isinstance(value, Server):
            now = datetime.now(timezone.utc)
            value.created_at = value.created_at or now
            value.updated_at = value.updated_at or now


def _server_create(**overrides) -> ServerCreate:
    values = {
        "name": "new-server",
        "host": "game.example",
        "ssh_user": "cs2",
        "ssh_password": "ssh-secret",
        "captcha_token": "captcha-token",
        "captcha_code": "ABCD",
        "ssh_host_key_algorithm": "ssh-ed25519",
        "ssh_host_key_fingerprint": "SHA256:trusted-host-key",
        "ssh_host_key_confirmed": True,
    }
    values.update(overrides)
    return ServerCreate(**values)


def _server_model(**overrides) -> Server:
    now = datetime.now(timezone.utc)
    values = {
        "id": 5,
        "user_id": 7,
        "name": "existing",
        "host": "old.example",
        "ssh_user": "cs2",
        "ssh_password": "secret",
        "auth_type": AuthType.PASSWORD,
        "status": ServerStatus.STOPPED,
        "created_at": now,
        "updated_at": now,
        "ssh_host_key_algorithm": "ssh-ed25519",
        "ssh_host_key_fingerprint": "SHA256:old-host-key",
    }
    values.update(overrides)
    return Server(**values)


def _patch_unique_server(monkeypatch) -> None:
    monkeypatch.setattr(Server, "get_by_name_and_user", AsyncMock(return_value=None))
    monkeypatch.setattr(
        Server,
        "get_by_host_directory_and_user",
        AsyncMock(return_value=None),
    )


@pytest.mark.asyncio
async def test_create_server_uses_confirmed_pin_and_reveals_api_key_once(monkeypatch):
    _patch_unique_server(monkeypatch)
    identity = SSHHostKeyIdentity("ssh-ed25519", "SHA256:trusted-host-key")
    monkeypatch.setattr(crud, "scan_ssh_host_key", AsyncMock(return_value=identity))
    monkeypatch.setattr(
        crud,
        "pinned_host_key_options",
        Mock(return_value={"known_hosts": b"strict-pin"}),
    )
    monkeypatch.setattr(crud.captcha_service, "validate_captcha", AsyncMock(return_value=True))
    connection = SimpleNamespace(
        run=AsyncMock(
            side_effect=[
                SimpleNamespace(exit_status=0),
                SimpleNamespace(exit_status=0),
                SimpleNamespace(exit_status=0),
            ]
        ),
        close=Mock(),
    )
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(crud.asyncssh, "connect", connect)
    monkeypatch.setattr(
        crud.SystemSettings,
        "get_or_create_settings",
        AsyncMock(return_value=SimpleNamespace(default_proxy_mode="direct")),
    )
    monkeypatch.setattr(crud, "generate_api_key", Mock(return_value="one-time-agent-key"))
    db = _CrudDatabase()
    response = Response()

    created = await crud.create_server(
        _server_create(),
        response,
        db,
        SimpleNamespace(id=7),
    )

    assert created.api_key == "one-time-agent-key"
    assert created.api_key_configured is True
    assert response.headers["cache-control"] == "no-store"
    assert created.ssh_host_key_fingerprint == identity.fingerprint
    assert db.commits == 2
    assert len(db.added) == 3
    assert connection.run.await_count == 3
    connection.close.assert_called_once()
    assert connect.await_args.kwargs["known_hosts"] == b"strict-pin"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [(TimeoutError(), 504), (OSError("refused"), 400)],
)
async def test_create_server_maps_host_key_scan_failures(monkeypatch, error, expected_status):
    _patch_unique_server(monkeypatch)
    monkeypatch.setattr(crud, "scan_ssh_host_key", AsyncMock(side_effect=error))

    with pytest.raises(HTTPException) as exc_info:
        await crud.create_server(
            _server_create(),
            Response(),
            _CrudDatabase(),
            SimpleNamespace(id=7),
        )

    assert exc_info.value.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [(TimeoutError(), 504), (ValueError("invalid key"), 400)],
)
async def test_new_host_key_scan_maps_transport_failures(monkeypatch, error, expected_status):
    monkeypatch.setattr(crud, "scan_ssh_host_key", AsyncMock(side_effect=error))

    with pytest.raises(HTTPException) as exc_info:
        await crud.scan_new_server_host_key(
            SSHHostKeyScanRequest(host="game.example", ssh_port=22),
            SimpleNamespace(id=7),
        )

    assert exc_info.value.status_code == expected_status


@pytest.mark.asyncio
async def test_host_key_scan_and_confirmation_report_pin_state(monkeypatch):
    identity = SSHHostKeyIdentity("ssh-ed25519", "SHA256:live-host-key")
    server = _server_model(ssh_host_key_fingerprint=identity.fingerprint, credential_revision=2)
    monkeypatch.setattr(crud, "scan_ssh_host_key", AsyncMock(return_value=identity))
    monkeypatch.setattr(crud, "get_server_with_permission", AsyncMock(return_value=server))
    db = _CrudDatabase()

    new_scan = await crud.scan_new_server_host_key(
        SSHHostKeyScanRequest(host=server.host),
        SimpleNamespace(id=7),
    )
    existing_scan = await crud.scan_existing_server_host_key(
        server.id,
        db,
        SimpleNamespace(id=7),
    )
    confirmed = await crud.confirm_existing_server_host_key(
        server.id,
        SSHHostKeyConfirmRequest(
            algorithm=identity.algorithm,
            fingerprint=identity.fingerprint,
        ),
        db,
        SimpleNamespace(id=7),
    )

    assert new_scan.configured is False
    assert existing_scan.configured is True
    assert existing_scan.matches_configured is True
    assert confirmed.credential_revision == 3
    assert db.commits == 3


@pytest.mark.asyncio
async def test_host_key_confirmation_rejects_changed_key(monkeypatch):
    server = _server_model(ssh_host_key_algorithm=None, ssh_host_key_fingerprint=None)
    monkeypatch.setattr(crud, "get_server_with_permission", AsyncMock(return_value=server))
    monkeypatch.setattr(
        crud,
        "scan_ssh_host_key",
        AsyncMock(return_value=SSHHostKeyIdentity("ssh-ed25519", "SHA256:new-host-key")),
    )
    db = _CrudDatabase()

    scan = await crud.scan_existing_server_host_key(server.id, db, SimpleNamespace(id=7))
    assert scan.configured is False
    assert scan.matches_configured is None

    with pytest.raises(HTTPException) as exc_info:
        await crud.confirm_existing_server_host_key(
            server.id,
            SSHHostKeyConfirmRequest(
                algorithm="ssh-ed25519",
                fingerprint="SHA256:different-key",
            ),
            db,
            SimpleNamespace(id=7),
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["scan", "confirm"])
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [(TimeoutError(), 504), (OSError("unreachable"), 400)],
)
async def test_existing_host_key_operations_map_scan_failures(
    monkeypatch, operation, error, expected_status
):
    server = _server_model()
    monkeypatch.setattr(crud, "get_server_with_permission", AsyncMock(return_value=server))
    monkeypatch.setattr(crud, "scan_ssh_host_key", AsyncMock(side_effect=error))
    db = _CrudDatabase()

    with pytest.raises(HTTPException) as exc_info:
        if operation == "scan":
            await crud.scan_existing_server_host_key(server.id, db, SimpleNamespace(id=7))
        else:
            await crud.confirm_existing_server_host_key(
                server.id,
                SSHHostKeyConfirmRequest(
                    algorithm="ssh-ed25519",
                    fingerprint="SHA256:trusted-host-key",
                ),
                db,
                SimpleNamespace(id=7),
            )

    assert exc_info.value.status_code == expected_status


@pytest.mark.asyncio
async def test_endpoint_change_clears_old_pin_and_increments_revision(monkeypatch):
    server = _server_model(credential_revision=4)
    monkeypatch.setattr(crud, "get_server_with_permission", AsyncMock(return_value=server))
    monkeypatch.setattr(crud.redis_manager, "clear_server_cache", AsyncMock())

    await crud.update_server(
        server_id=server.id,
        server_data=ServerUpdate(host="new.example"),
        ssh_manager=SimpleNamespace(),
        db=_CrudDatabase(),
        current_user=SimpleNamespace(id=7),
    )

    assert server.host == "new.example"
    assert server.ssh_host_key_algorithm is None
    assert server.ssh_host_key_fingerprint is None
    assert server.credential_revision == 5


class _TicketRedis:
    def __init__(self, payload) -> None:
        self.payload = payload

    async def eval(self, *_args):
        return self.payload


def _download_ticket_request(client) -> Request:
    container = SimpleNamespace(
        redis=SimpleNamespace(client=client),
        settings=file_common.settings,
    )
    app = SimpleNamespace(state=SimpleNamespace(container=container))
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [b"{not-json", b'{"server_id":1,"path":"/x","user_id":"invalid"}'],
)
async def test_download_ticket_rejects_malformed_or_invalid_payload(payload):
    request = _download_ticket_request(_TicketRedis(payload))

    assert await file_common._consume_download_ticket(request, "ticket", 1, "/x") is None


@pytest.mark.asyncio
async def test_download_auth_maps_coordination_failure_to_service_unavailable(monkeypatch):
    monkeypatch.setattr(
        file_common,
        "_consume_download_ticket",
        AsyncMock(side_effect=file_common.DownloadTicketStoreUnavailable("redis unavailable")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await file_common.get_current_active_user_for_download(
            request=_download_ticket_request(_TicketRedis(None)),
            server_id=1,
            path="/srv/file",
            ticket="one-time-ticket",
            authorization=None,
            db=object(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "redis unavailable"


class _CommandConnection:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.run = AsyncMock(side_effect=self._run)

    async def _run(self, *_args, **_kwargs):
        if self.fail:
            raise asyncssh.ConnectionLost("connection reset")
        return SimpleNamespace(exit_status=0, stdout="ok", stderr="")


class _Stream:
    def __init__(self, lines=()) -> None:
        self.lines = iter(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.lines)
        except StopIteration:
            raise StopAsyncIteration from None


class _StreamingConnection:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def create_process(self, _command):
        if self.fail:
            raise asyncssh.ConnectionLost("stream reset")
        return SimpleNamespace(
            stdout=_Stream(["stdout\n"]),
            stderr=_Stream(["stderr\n"]),
            wait=AsyncMock(return_value=SimpleNamespace(exit_status=0)),
        )


@pytest.mark.asyncio
async def test_connection_mixin_uses_generation_bound_leases(monkeypatch):
    from services.ssh import connection as connection_module

    server = _server_model()
    pooled_connection = _CommandConnection()
    lease = SimpleNamespace(connection=pooled_connection, release=AsyncMock())
    acquire = AsyncMock(return_value=(True, lease, "connected"))
    monkeypatch.setattr(connection_module.ssh_connection_pool, "acquire_lease", acquire)
    manager = SSHManager()

    assert await manager.connect(server) == (True, "connected")
    assert manager.connection_lease is lease
    assert manager.conn is pooled_connection

    await manager.disconnect()
    lease.release.assert_awaited_once()
    assert manager.conn is None
    assert manager.connection_lease is None
    assert manager.current_server is None


@pytest.mark.asyncio
async def test_command_and_sftp_reconnect_replace_the_active_lease(monkeypatch):
    from services.ssh import connection as connection_module

    server = _server_model()
    old_lease = SimpleNamespace(connection=object())
    new_connection = _CommandConnection()
    new_lease = SimpleNamespace(connection=new_connection)
    reconnect = AsyncMock(return_value=(True, new_lease, "reconnected"))
    monkeypatch.setattr(connection_module.ssh_connection_pool, "reconnect_lease", reconnect)

    manager = SSHManager()
    manager.current_server = server
    manager.connection_lease = old_lease
    manager.conn = _CommandConnection(fail=True)

    assert await manager.execute_command("uptime") == (True, "ok", "")
    assert manager.connection_lease is new_lease
    reconnect.assert_awaited_with(server, old_lease)

    retry = AsyncMock(return_value="retried")
    manager.connection_lease = old_lease
    assert (
        await manager._handle_sftp_error_with_reconnect(
            asyncssh.ConnectionLost("broken pipe"),
            server,
            "read file",
            retry,
        )
        == "retried"
    )
    retry.assert_awaited_once()
    assert manager.connection_lease is new_lease


@pytest.mark.asyncio
async def test_streaming_reconnect_retries_with_new_lease(monkeypatch):
    from services.ssh import connection as connection_module

    server = _server_model()
    old_lease = SimpleNamespace(connection=object())
    new_connection = _StreamingConnection()
    new_lease = SimpleNamespace(connection=new_connection)
    reconnect = AsyncMock(return_value=(True, new_lease, "reconnected"))
    monkeypatch.setattr(connection_module.ssh_connection_pool, "reconnect_lease", reconnect)
    manager = SSHManager()
    manager.current_server = server
    manager.connection_lease = old_lease
    manager.conn = _StreamingConnection(fail=True)

    success, stdout, stderr = await manager.execute_command_streaming("stream")

    assert success is True
    assert stdout == "stdout"
    assert stderr == "stderr"
    assert manager.connection_lease is new_lease
    reconnect.assert_awaited_once_with(server, old_lease)


@pytest.mark.asyncio
async def test_disconnect_without_lease_uses_connection_identity(monkeypatch):
    from services.ssh import connection as connection_module

    server = _server_model()
    connection = object()
    release = AsyncMock()
    monkeypatch.setattr(connection_module.ssh_connection_pool, "release_connection", release)
    manager = SSHManager()
    manager.conn = connection
    manager.current_server = server

    await manager.disconnect()

    release.assert_awaited_once_with(server, connection)
