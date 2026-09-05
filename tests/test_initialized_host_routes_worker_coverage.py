"""覆盖已初始化主机的 v1 路由和 SSH operation worker。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import asyncssh
import pytest
from fastapi import HTTPException

from api.routes.v1 import setup
from api.routes.v1.operation_runner import initialized_hosts
from api.routes.v1.schemas import (
    AutoSetupRequest,
    InitializedHostDeployRequest,
    InitializedHostOperationRequest,
)
from services.initialized_server_service import (
    InitializedServerAccessDenied,
    InitializedServerRecord,
    _ResolvedRecord,
)
from services.server_operation_hub import ServerOperationConflict


def _record(*, key="7", user_id=9, host="host.example"):
    return InitializedServerRecord(
        key=key,
        user_id=user_id,
        name="saved",
        host=host,
        ssh_port=2222,
        ssh_user="steam",
        ssh_password="secret",
        game_directory="/srv/cs2",
        created_at=1_700_000_000,
    )


def _operation(*, server_id=-7, action="test_initialized_ssh"):
    return {
        "operation_id": str(uuid4()),
        "server_id": server_id,
        "action": action,
        "status": "queued",
        "success": None,
        "message": None,
        "server_status": None,
        "actor_user_id": 9,
        "started_at": datetime.now(timezone.utc),
        "completed_at": None,
        "command": "ssh-test initialized-host:7",
    }


class _DbContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return None


class _Hub:
    def __init__(self, record=None):
        self.record = record
        self.finished = []
        self.running = []
        self.emitted = []

    async def create(self, **kwargs):
        return {**_operation(), **kwargs, "operation_id": "queued-op"}

    async def get(self, _operation_id):
        return self.record

    async def get_current(self, _server_id):
        return self.record

    async def mark_running(self, operation_id):
        self.running.append(operation_id)

    async def emit(self, operation_id, *args, **kwargs):
        self.emitted.append((operation_id, args, kwargs))

    async def finish(self, operation_id, **kwargs):
        self.finished.append((operation_id, kwargs))


class _Db:
    def __init__(self, user=None):
        self.user = user or SimpleNamespace(id=9, is_active=True)

    async def get(self, *_args):
        return self.user


@pytest.mark.asyncio
async def test_setup_helpers_and_basic_saved_host_routes(monkeypatch):
    saved = _record()
    assert setup._to_list_item(saved).key == "7"
    assert setup._to_credentials(saved).ssh_password == "secret"

    monkeypatch.setattr(setup, "list_saved_initialized_servers", AsyncMock(return_value=[saved]))
    listed = await setup.list_initialized_hosts(_Db(), SimpleNamespace(id=9))
    assert listed[0].host == "host.example"

    monkeypatch.setattr(setup, "delete_saved_initialized_servers", AsyncMock(return_value=2))
    deleted = await setup.batch_delete_initialized_hosts(
        SimpleNamespace(ids=[1, 2]), _Db(), SimpleNamespace(id=9)
    )
    assert deleted.success and deleted.message == "Deleted 2 initialized host(s)"

    monkeypatch.setattr(setup, "resolve_initialized_server", AsyncMock(return_value=_ResolvedRecord(saved)))
    assert (await setup._resolve_owned(_Db(), "7", 9)).host == "host.example"
    monkeypatch.setattr(setup, "resolve_initialized_server", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as missing:
        await setup._resolve_owned(_Db(), "7", 9)
    assert missing.value.status_code == 404
    monkeypatch.setattr(
        setup,
        "resolve_initialized_server",
        AsyncMock(side_effect=InitializedServerAccessDenied),
    )
    with pytest.raises(HTTPException) as forbidden:
        await setup._resolve_owned(_Db(), "7", 9)
    assert forbidden.value.status_code == 403


@pytest.mark.asyncio
async def test_setup_operation_views_current_stream_and_not_found_paths(monkeypatch):
    saved = _record()
    operation = _operation()
    monkeypatch.setattr(setup, "_resolve_owned", AsyncMock(return_value=saved))
    monkeypatch.setattr(setup, "enqueue_initialized_host_ssh_test", AsyncMock(return_value=operation))
    started = await setup.start_initialized_host_operation(
        7, InitializedHostOperationRequest(action="test_ssh"), _Db(), SimpleNamespace(id=9)
    )
    assert started.operation_id == operation["operation_id"]

    monkeypatch.setattr(
        setup,
        "enqueue_initialized_host_ssh_test",
        AsyncMock(side_effect=ServerOperationConflict("busy")),
    )
    with pytest.raises(HTTPException) as conflict:
        await setup.start_initialized_host_operation(
            7, InitializedHostOperationRequest(action="test_ssh"), _Db(), SimpleNamespace(id=9)
        )
    assert conflict.value.status_code == 409

    hub = SimpleNamespace(get_current=AsyncMock(return_value=None))
    monkeypatch.setattr(setup, "server_operation_hub", hub)
    assert await setup.get_current_initialized_host_operation(7, _Db(), SimpleNamespace(id=9)) is None
    hub.get_current.return_value = {**operation, "server_id": -8}
    assert await setup.get_current_initialized_host_operation(7, _Db(), SimpleNamespace(id=9)) is None
    hub.get_current.return_value = operation
    assert (await setup.get_current_initialized_host_operation(7, _Db(), SimpleNamespace(id=9))).operation_id

    operation_id = UUID(operation["operation_id"])
    hub.get = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as not_found:
        await setup.get_initialized_host_operation(7, operation_id, _Db(), SimpleNamespace(id=9))
    assert not_found.value.status_code == 404
    hub.get.return_value = {**operation, "server_id": -8}
    with pytest.raises(HTTPException):
        await setup.get_initialized_host_operation(7, operation_id, _Db(), SimpleNamespace(id=9))
    hub.get.return_value = operation
    assert (await setup.get_initialized_host_operation(7, operation_id, _Db(), SimpleNamespace(id=9))).action == "test_ssh"

    cancelled = {**operation, "status": "failed", "success": False, "message": "stopped"}
    hub.cancel = AsyncMock(return_value=cancelled)
    stopped = await setup.cancel_initialized_host_operation(
        7, operation_id, _Db(), SimpleNamespace(id=9)
    )
    assert stopped.status == "failed"
    hub.cancel.assert_awaited_once_with(
        operation["operation_id"], message="Operation force-stopped by operator"
    )


@pytest.mark.asyncio
async def test_setup_stream_delete_deploy_and_auto_setup_paths(monkeypatch):
    saved = _record()
    operation = _operation()
    user = SimpleNamespace(id=9)
    monkeypatch.setattr(setup, "_resolve_owned", AsyncMock(return_value=saved))
    hub = SimpleNamespace(get=AsyncMock(return_value=operation))
    monkeypatch.setattr(setup, "server_operation_hub", hub)
    monkeypatch.setattr(setup, "async_session_maker", lambda: _DbContext(_Db()))
    response = await setup.stream_initialized_host_operation_events(
        7, UUID(operation["operation_id"]), SimpleNamespace(), user, after=4
    )
    assert response.media_type == "text/event-stream"

    monkeypatch.setattr(setup, "delete_saved_initialized_server", AsyncMock(return_value=True))
    result = await setup.delete_initialized_host("7", _Db(), user)
    assert result.success
    monkeypatch.setattr(setup, "delete_saved_initialized_server", AsyncMock(return_value=False))
    with pytest.raises(HTTPException) as delete_error:
        await setup.delete_initialized_host("7", _Db(), user)
    assert delete_error.value.status_code == 500
    monkeypatch.setattr(
        setup,
        "delete_saved_initialized_server",
        AsyncMock(side_effect=InitializedServerAccessDenied),
    )
    with pytest.raises(HTTPException) as delete_forbidden:
        await setup.delete_initialized_host("7", _Db(), user)
    assert delete_forbidden.value.status_code == 403

    monkeypatch.setattr(
        setup,
        "create_server_record",
        AsyncMock(return_value=SimpleNamespace(id=12)),
    )
    deploy_operation = _operation(server_id=12, action="deploy")
    monkeypatch.setattr(setup, "enqueue_server_operation", AsyncMock(return_value=deploy_operation))
    deployed = await setup.deploy_from_initialized_host(
        7,
        InitializedHostDeployRequest(name="new-server"),
        _Db(),
        user,
        SimpleNamespace(),
    )
    assert deployed.server_id == 12 and deployed.operation.action == "deploy"
    monkeypatch.setattr(setup, "enqueue_server_operation", AsyncMock(side_effect=ServerOperationConflict("busy")))
    with pytest.raises(HTTPException) as deploy_conflict:
        await setup.deploy_from_initialized_host(
            7,
            InitializedHostDeployRequest(name="new-server"),
            _Db(),
            user,
            SimpleNamespace(),
        )
    assert deploy_conflict.value.status_code == 409

    monkeypatch.setattr(setup, "validate_cs2_username", lambda value: value)
    monkeypatch.setattr(setup, "generate_secure_password", lambda: "generated")
    monkeypatch.setattr(setup, "build_manual_setup_script", lambda **_kwargs: "#!/bin/sh")
    script = await setup.read_manual_setup_script(user, cs2_username="custom")
    assert script.password == "generated"
    monkeypatch.setattr(setup, "validate_cs2_username", lambda _value: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(HTTPException) as script_error:
        await setup.read_manual_setup_script(user, cs2_username="bad")
    assert script_error.value.status_code == 422

    body = AutoSetupRequest(
        name="new",
        host="host",
        ssh_user="root",
        ssh_password="pw",
        sudo_password="sudo",
    )
    monkeypatch.setattr(
        setup,
        "auto_setup_server",
        AsyncMock(return_value=SimpleNamespace(
            success=True,
            message="ok",
            cs2_username="cs2server",
            cs2_password="pw2",
            game_directory="/cs2",
            logs=None,
            initialized_server_id="7",
        )),
    )
    auto = await setup.run_auto_setup(body, user, _Db())
    assert auto.success and auto.logs == []


@pytest.mark.asyncio
async def test_initialized_host_worker_covers_success_and_failures(monkeypatch):
    record = _operation()
    hub = _Hub(record)
    monkeypatch.setattr(initialized_hosts, "server_operation_hub", hub)
    monkeypatch.setattr(initialized_hosts, "async_session_maker", lambda: _DbContext(_Db()))
    saved = _record()
    monkeypatch.setattr(
        initialized_hosts,
        "resolve_initialized_server",
        AsyncMock(return_value=_ResolvedRecord(saved, database_record=SimpleNamespace(id=7))),
    )

    class _Connection:
        def __init__(self, exit_status=0):
            self.exit_status = exit_status
            self.closed = False

        async def run(self, *_args, **_kwargs):
            return SimpleNamespace(exit_status=self.exit_status)

        def close(self):
            self.closed = True

        async def wait_closed(self):
            return None

    connection = _Connection()
    monkeypatch.setattr(initialized_hosts.asyncssh, "connect", AsyncMock(return_value=connection))
    monkeypatch.setattr(initialized_hosts, "_dispatch", AsyncMock(side_effect=lambda item, _factory: item))
    await initialized_hosts.enqueue_initialized_host_ssh_test(initialized_server_id=7, actor_user_id=9)
    assert hub.record["action"] == "test_initialized_ssh"
    await initialized_hosts.run_initialized_host_ssh_test(operation_id="op", initialized_server_id=7)
    assert hub.finished[-1][1]["success"] is True and connection.closed

    for error, expected in (
        (asyncssh.PermissionDenied("denied"), "SSH authentication failed"),
        (TimeoutError(), "SSH connection timed out"),
        (asyncssh.Error(1, "network"), "SSH connection failed"),
        (ServerOperationConflict("busy"), "busy"),
        (RuntimeError("unexpected"), "unexpectedly"),
    ):
        hub = _Hub(record)
        monkeypatch.setattr(initialized_hosts, "server_operation_hub", hub)
        monkeypatch.setattr(initialized_hosts, "asyncssh", SimpleNamespace(
            connect=AsyncMock(side_effect=error),
            PermissionDenied=asyncssh.PermissionDenied,
            Error=asyncssh.Error,
        ))
        await initialized_hosts.run_initialized_host_ssh_test(operation_id="op", initialized_server_id=7)
        assert expected in hub.finished[-1][1]["message"]

    hub = _Hub(record)
    monkeypatch.setattr(initialized_hosts, "server_operation_hub", hub)
    monkeypatch.setattr(initialized_hosts, "asyncssh", SimpleNamespace(
        connect=AsyncMock(), PermissionDenied=asyncssh.PermissionDenied, Error=asyncssh.Error
    ))
    monkeypatch.setattr(initialized_hosts, "resolve_initialized_server", AsyncMock(return_value=None))
    await initialized_hosts.run_initialized_host_ssh_test(operation_id="op", initialized_server_id=7)
    assert "deleted" in hub.finished[-1][1]["message"]

    hub = _Hub(record)
    monkeypatch.setattr(initialized_hosts, "server_operation_hub", hub)
    monkeypatch.setattr(initialized_hosts, "async_session_maker", lambda: _DbContext(_Db(SimpleNamespace(id=9, is_active=False))))
    await initialized_hosts.run_initialized_host_ssh_test(operation_id="op", initialized_server_id=7)
    assert "no longer" in hub.finished[-1][1]["message"]
