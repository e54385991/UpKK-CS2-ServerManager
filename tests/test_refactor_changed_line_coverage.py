from __future__ import annotations

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException, WebSocketDisconnect

from api.routes.actions import batch as batch_routes
from api.routes.actions import common as action_common
from api.routes.actions import deployment as deployment_routes
from api.routes.servers import maintenance
from modules import AuthType, Server, User
from modules.schemas import (
    BatchInstallPluginsRequest,
    BatchSendCommandRequest,
    CleanupDeleteRequest,
    S3RestoreRequest,
)


class _CommitSession:
    def __init__(self):
        self.commit_count = 0

    async def commit(self):
        self.commit_count += 1


class _CapturingSupervisor:
    def __init__(self):
        self.tasks = []
        self.names = []

    def create(self, coroutine, *, name):
        self.tasks.append(coroutine)
        self.names.append(name)
        return coroutine

    async def drain(self):
        for coroutine in self.tasks:
            await coroutine


def _background_server(
    server_id: int,
    *,
    user_id: int = 7,
    session_manager: str = "tmux",
) -> Server:
    return Server(
        id=server_id,
        user_id=user_id,
        name=f"server-{server_id}",
        host=f"server-{server_id}.example.com",
        ssh_user="cs2",
        auth_type=AuthType.PASSWORD,
        session_manager=session_manager,
    )


@pytest.mark.asyncio
async def test_batch_plugin_and_command_routes_forward_app_session_factory(monkeypatch):
    session = _CommitSession()
    session_factory = Mock()
    ssh_pool = object()
    http_resource = SimpleNamespace(
        get=AsyncMock(),
        post=AsyncMock(),
        borrow_client=Mock(),
        download_file=AsyncMock(),
    )
    supervisor = _CapturingSupervisor()
    http_request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                task_supervisor=supervisor,
                container=SimpleNamespace(
                    database=SimpleNamespace(session_factory=session_factory),
                    ssh_pool=ssh_pool,
                    http=http_resource,
                ),
            )
        )
    )
    current_user = SimpleNamespace(id=9, is_admin=False)
    initialize = AsyncMock(return_value=True)
    install = AsyncMock()
    command = AsyncMock()

    async def owned(_db, server_ids, _user_id):
        return list(server_ids)

    async def run_now(_server_id, _user_id, _batch_id, _operation, callback):
        await callback()

    monkeypatch.setattr(batch_routes, "_get_owned_server_ids", owned)
    monkeypatch.setattr(batch_routes, "_reserve_batch_capacity", AsyncMock())
    monkeypatch.setattr(batch_routes.redis_manager, "initialize_batch_action", initialize)
    monkeypatch.setattr(batch_routes, "_run_bounded_batch_operation", run_now)
    monkeypatch.setattr(batch_routes, "execute_single_server_plugins", install)
    monkeypatch.setattr(batch_routes, "execute_single_server_command", command)
    monkeypatch.setattr(batch_routes.secrets, "token_hex", lambda _length: "batch-token")

    plugins_response = await batch_routes.batch_install_plugins(
        BatchInstallPluginsRequest(server_ids=[1, 2], plugins=["metamod"]),
        http_request,
        db=session,
        current_user=current_user,
    )
    command_response = await batch_routes.batch_send_command(
        BatchSendCommandRequest(server_ids=[3], command="status"),
        http_request,
        db=session,
        current_user=current_user,
    )
    await supervisor.drain()

    assert plugins_response.server_count == 2
    assert command_response.server_count == 1
    assert session.commit_count == 2
    assert initialize.await_count == 2
    assert supervisor.names == [
        "batch-plugin-install-batch-token-1",
        "batch-plugin-install-batch-token-2",
        "batch-command-batch-token-3",
    ]
    assert install.await_count == 2
    assert all(
        call.kwargs["session_factory"] is session_factory for call in install.await_args_list
    )
    ssh_manager_factory = install.await_args_list[0].kwargs["ssh_manager_factory"]
    assert all(
        call.kwargs["ssh_manager_factory"] is ssh_manager_factory
        for call in install.await_args_list
    )
    manager = ssh_manager_factory()
    assert manager.connection_pool is ssh_pool
    assert manager.http_resource is http_resource
    command_ssh_manager_factory = command.await_args.kwargs["ssh_manager_factory"]
    command_manager = command_ssh_manager_factory()
    assert command_manager.connection_pool is ssh_pool
    assert command_manager.http_resource is http_resource
    command.assert_awaited_once_with(
        3,
        "status",
        9,
        False,
        "batch-token",
        session_factory=session_factory,
        ssh_manager_factory=command_ssh_manager_factory,
    )


@pytest.mark.asyncio
async def test_batch_empty_and_legacy_owner_rejection_branches(monkeypatch):
    assert await batch_routes._get_owned_server_ids(_CommitSession(), [], user_id=4) == []

    monkeypatch.setattr(
        batch_routes.redis_manager,
        "get_batch_action_status",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        batch_routes.redis_manager,
        "get_legacy_batch_action_status",
        AsyncMock(return_value={"not-an-id": {"status": "success"}}),
    )

    with pytest.raises(HTTPException) as exc_info:
        await batch_routes.get_batch_action_status(
            "legacy",
            db=_CommitSession(),
            current_user=SimpleNamespace(id=4),
        )

    assert exc_info.value.status_code == 404


class _CommandSession:
    def __init__(self, server):
        self.server = server
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True

    async def get(self, _model, _server_id):
        return self.server


class _CommandManager:
    def __init__(self, session, *, connect=(True, "connected"), execute=(True, "", "")):
        self.session = session
        self.connect_result = connect
        self.execute_result = execute
        self.disconnect_count = 0

    async def connect(self, _server):
        assert self.session.closed is True
        return self.connect_result

    async def execute_command(self, _command, timeout=10):
        return self.execute_result

    async def disconnect(self):
        self.disconnect_count += 1


@pytest.mark.asyncio
async def test_batch_command_owner_failure_and_ssh_finally_branches(monkeypatch):
    status_updates = []

    async def record_status(batch_id, server_id, state, message):
        status_updates.append((batch_id, server_id, state, message))
        return True

    monkeypatch.setattr(action_common.redis_manager, "set_batch_action_status", record_status)

    denied_server = SimpleNamespace(user_id=99, session_manager="tmux")
    denied_session = _CommandSession(denied_server)
    await action_common.execute_single_server_command(
        1,
        "status",
        user_id=7,
        is_admin=False,
        batch_id="denied",
        session_factory=lambda: denied_session,
    )
    assert status_updates[-1][-1] == "Access denied"

    failed_session = _CommandSession(_background_server(2))
    failed_manager = _CommandManager(
        failed_session,
        connect=(False, "unreachable"),
    )
    monkeypatch.setattr(action_common, "SSHManager", lambda: failed_manager)
    await action_common.execute_single_server_command(
        2,
        "status",
        user_id=7,
        is_admin=False,
        batch_id="connect-failed",
        session_factory=lambda: failed_session,
    )
    assert status_updates[-1][-1] == "SSH connection failed: unreachable"

    no_session_db = _CommandSession(_background_server(3))
    no_session_manager = _CommandManager(no_session_db)
    monkeypatch.setattr(action_common, "SSHManager", lambda: no_session_manager)
    monkeypatch.setattr(
        action_common,
        "find_running_session_manager",
        AsyncMock(return_value=None),
    )
    await action_common.execute_single_server_command(
        3,
        "status",
        user_id=7,
        is_admin=False,
        batch_id="not-running",
        session_factory=lambda: no_session_db,
    )
    assert "Game server is not running" in status_updates[-1][-1]
    assert no_session_manager.disconnect_count == 1

    command_db = _CommandSession(_background_server(4))
    command_manager = _CommandManager(
        command_db,
        execute=(False, "", "command rejected"),
    )
    monkeypatch.setattr(action_common, "SSHManager", lambda: command_manager)
    monkeypatch.setattr(
        action_common,
        "find_running_session_manager",
        AsyncMock(return_value="tmux"),
    )
    monkeypatch.setattr(action_common, "send_keys_command", lambda *_args: "send command")
    await action_common.execute_single_server_command(
        4,
        "status",
        user_id=7,
        is_admin=False,
        batch_id="command-failed",
        session_factory=lambda: command_db,
    )
    assert status_updates[-1][-1] == "Failed to send command: command rejected"
    assert command_manager.disconnect_count == 1

    def broken_factory():
        raise RuntimeError("session factory failed")

    await action_common.execute_single_server_command(
        5,
        "status",
        user_id=7,
        is_admin=False,
        batch_id="background-error",
        session_factory=broken_factory,
    )
    assert status_updates[-1][-1] == "session factory failed"


def test_spawn_action_task_uses_legacy_registry_without_supervisor(monkeypatch):
    sentinel = object()

    def create(coroutine):
        coroutine.close()
        return sentinel

    monkeypatch.setattr(action_common.action_task_registry, "create", create)

    async def work():
        return None

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    assert action_common._spawn_action_task(request, work(), name="legacy") is sentinel


class _BackgroundSession:
    def __init__(self):
        self.added = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _model, _record_id):
        return None

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_background_action_and_plugin_workers_use_injected_sessions(monkeypatch):
    sessions = []
    status_updates = AsyncMock(return_value=True)

    def session_factory():
        session = _BackgroundSession()
        sessions.append(session)
        return session

    monkeypatch.setattr(action_common.redis_manager, "set_batch_action_status", status_updates)
    monkeypatch.setattr(
        action_common.Server,
        "get_by_id_and_user",
        AsyncMock(side_effect=[None, _background_server(2)]),
    )

    await action_common.execute_single_server_action(
        1,
        "stop",
        user_id=7,
        is_admin=False,
        batch_id="missing-action-server",
        session_factory=session_factory,
    )

    monkeypatch.setattr(action_common, "SSHManager", lambda: SimpleNamespace())
    monkeypatch.setattr(
        action_common,
        "send_discord_action_notification",
        AsyncMock(),
    )
    await action_common.execute_single_server_plugins(
        2,
        ["unknown-plugin"],
        user_id=7,
        is_admin=False,
        batch_id="unknown-plugin",
        session_factory=session_factory,
    )

    assert len(sessions) == 3
    assert sessions[-1].committed is True
    assert sessions[-1].added[0].action == "install_unknown-plugin"
    assert status_updates.await_count >= 5


class _FakeWebSocket:
    def __init__(self, supervisor):
        self.scope = {
            "app": SimpleNamespace(
                state=SimpleNamespace(task_supervisor=supervisor),
            )
        }
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)

    async def receive_text(self):
        raise WebSocketDisconnect()


@pytest.mark.asyncio
async def test_deployment_websocket_injects_supervisor_recovers_and_disconnects(monkeypatch):
    supervisor = object()
    websocket = _FakeWebSocket(supervisor)
    connect = AsyncMock()
    disconnect = Mock()
    monkeypatch.setattr(
        deployment_routes,
        "authenticate_websocket",
        AsyncMock(return_value=(SimpleNamespace(id=1), SimpleNamespace(id=8))),
    )
    monkeypatch.setattr(
        deployment_routes,
        "deployment_ws",
        SimpleNamespace(connect=connect, disconnect=disconnect),
    )
    monkeypatch.setattr(
        deployment_routes.redis_manager,
        "get_deployment_progress",
        AsyncMock(return_value=[{"type": "status", "message": "recovered"}]),
    )

    await deployment_routes.deployment_status_websocket(websocket, 8)

    connect.assert_awaited_once_with(websocket, 8, task_supervisor=supervisor)
    assert websocket.sent[0]["type"] == "info"
    assert websocket.sent[1] == {"type": "status", "message": "recovered"}
    disconnect.assert_called_once_with(websocket, 8)


class _LockService:
    def __init__(self):
        self.calls = []

    @asynccontextmanager
    async def get(self, server_id, **kwargs):
        self.calls.append((server_id, kwargs))
        yield


@pytest.mark.asyncio
async def test_cleanup_scan_always_releases_ssh(monkeypatch):
    ssh_manager = SimpleNamespace(disconnect=AsyncMock())
    monkeypatch.setattr(
        maintenance,
        "get_server_with_permission",
        AsyncMock(return_value=SimpleNamespace(id=12)),
    )
    monkeypatch.setattr(maintenance, "SSHManager", lambda: ssh_manager)
    monkeypatch.setattr(
        maintenance.game_cleanup_service,
        "scan",
        AsyncMock(return_value=(True, {"safe_items": []}, None)),
    )

    result = await maintenance.scan_server_cleanup(
        12,
        db=SimpleNamespace(),
        current_user=SimpleNamespace(id=1),
    )

    assert result == {"safe_items": []}
    ssh_manager.disconnect.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_error", (None, "unsafe deletion"))
async def test_cleanup_delete_uses_destructive_lock_and_always_disconnects(
    monkeypatch,
    delete_error,
):
    lock_service = _LockService()
    ssh_manager = SimpleNamespace(disconnect=AsyncMock())
    monkeypatch.setattr(
        maintenance,
        "get_server_with_permission",
        AsyncMock(return_value=SimpleNamespace(id=12)),
    )
    monkeypatch.setattr(maintenance, "maintenance_lock_service", lock_service)
    monkeypatch.setattr(maintenance, "SSHManager", lambda: ssh_manager)
    monkeypatch.setattr(
        maintenance.game_cleanup_service,
        "delete",
        AsyncMock(return_value=(delete_error is None, {"success": True}, delete_error)),
    )

    request = CleanupDeleteRequest(mode="safe", paths=["cache/file"])
    if delete_error:
        with pytest.raises(HTTPException) as exc_info:
            await maintenance.delete_server_cleanup_items(
                12,
                request,
                db=SimpleNamespace(),
                current_user=SimpleNamespace(id=1),
            )
        assert exc_info.value.status_code == 400
    else:
        result = await maintenance.delete_server_cleanup_items(
            12,
            request,
            db=SimpleNamespace(),
            current_user=SimpleNamespace(id=1),
        )
        assert result == {"success": True}

    assert lock_service.calls == [
        (
            12,
            {
                "operation": "server_cleanup_delete",
                "wait": False,
                "ttl": 7200,
            },
        )
    ]
    ssh_manager.disconnect.assert_awaited_once()


class _RestoreSession:
    def __init__(self):
        self.committed = False

    async def commit(self):
        self.committed = True


class _RestoreSSH:
    def __init__(self, failure_stage=None):
        self.failure_stage = failure_stage
        self.last_plugin_backup = {"path": "/remote/safety.tar.gz"}
        self.disconnect = AsyncMock()

    async def backup_plugins(self, _server):
        return (
            (False, "safety failed") if self.failure_stage == "safety" else (True, "safety created")
        )

    async def upload_file(self, _local_path, _remote_path, _server):
        return (False, "upload failed") if self.failure_stage == "upload" else (True, "uploaded")

    async def extract_archive(self, _archive, _target, _server, *, overwrite):
        assert overwrite is True
        return (False, "extract failed") if self.failure_stage == "extract" else (True, "extracted")


def _restore_models():
    owner = User(
        id=5,
        username="owner",
        email="owner@example.com",
        hashed_password="hash",
    )
    server = Server(
        id=21,
        user_id=5,
        name="restore-target",
        host="server.example",
        ssh_user="cs2",
        auth_type=AuthType.PASSWORD,
        ssh_password="secret",
        game_directory="/srv/cs2",
    )
    return owner, server


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", (None, "download", "safety", "upload", "extract"))
async def test_s3_restore_lock_detaches_db_maps_errors_and_cleans_up(
    monkeypatch,
    failure_stage,
):
    owner, server = _restore_models()
    db = _RestoreSession()
    lock_service = _LockService()
    ssh_manager = _RestoreSSH(failure_stage)
    local_paths = []

    async def download(_owner, _server, _key, local_path):
        assert db.committed is True
        assert _owner is not owner
        assert _server is not server
        local_paths.append(local_path)
        return (False, "download failed") if failure_stage == "download" else (True, "")

    s3_service = SimpleNamespace(
        validate_object_key=lambda *_args: True,
        safe_object_filename=lambda _key: "plugins.tar.gz",
        download_backup=download,
    )
    monkeypatch.setattr(maintenance, "get_server_with_permission", AsyncMock(return_value=server))
    monkeypatch.setattr(maintenance, "get_server_owner_user", AsyncMock(return_value=owner))
    monkeypatch.setattr(maintenance, "maintenance_lock_service", lock_service)
    monkeypatch.setattr(maintenance, "SSHManager", lambda: ssh_manager)

    if failure_stage is None:
        result = await maintenance.restore_server_s3_backup(
            21,
            S3RestoreRequest(object_key="users/5/servers/21/plugins.tar.gz"),
            db=db,
            current_user=owner,
            s3_service=s3_service,
        )
        assert result["success"] is True
        assert result["safety_backup"] == {"path": "/remote/safety.tar.gz"}
    else:
        with pytest.raises(HTTPException) as exc_info:
            await maintenance.restore_server_s3_backup(
                21,
                S3RestoreRequest(object_key="users/5/servers/21/plugins.tar.gz"),
                db=db,
                current_user=owner,
                s3_service=s3_service,
            )
        assert exc_info.value.status_code == 400
        assert failure_stage in str(exc_info.value.detail).lower()

    assert db.committed is True
    assert lock_service.calls[0][1]["operation"] == "s3_restore"
    ssh_manager.disconnect.assert_awaited_once()
    assert local_paths
    assert not os.path.exists(os.path.dirname(local_paths[0]))


@pytest.mark.asyncio
async def test_release_ssh_manager_logs_disconnect_failure(monkeypatch):
    warning = Mock()
    monkeypatch.setattr(maintenance.logger, "warning", warning)
    manager = SimpleNamespace(disconnect=AsyncMock(side_effect=RuntimeError("close failed")))

    await maintenance._release_ssh_manager(manager, "coverage operation")

    warning.assert_called_once()
