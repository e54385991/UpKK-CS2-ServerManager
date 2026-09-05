"""Cover the action router's deterministic branches without remote I/O."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes.actions import deployment
from modules.models import AuthType, Server, ServerStatus
from modules.schemas import ServerAction


def _server(**overrides) -> Server:
    values = {
        "id": 301,
        "user_id": 7,
        "name": "coverage-server",
        "host": "127.0.0.1",
        "ssh_user": "steam",
        "auth_type": AuthType.PASSWORD,
        "status": ServerStatus.STOPPED,
        "game_directory": "/srv/cs2",
    }
    values.update(overrides)
    return Server(**values)


class _DB:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.refreshed = []

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def refresh(self, value):
        self.refreshed.append(value)


class _SSH:
    def __init__(self, *, success=True, status="running", raise_on=None):
        self.success = success
        self.status = status
        self.raise_on = raise_on
        self.calls = []
        self.connect = AsyncMock(return_value=(True, "connected"))
        self.execute_command = AsyncMock(return_value=(True, "", ""))
        self.disconnect = AsyncMock()

    async def _result(self, name, callback=None):
        self.calls.append(name)
        if self.raise_on == name:
            raise RuntimeError("remote failure")
        if callback is not None:
            await callback(f"{name} progress")
        return self.success, f"{name} message"

    async def check_session_manager_available(self, _server):
        self.calls.append("check_session_manager_available")
        return True, "ready"

    async def deploy_cs2_server(self, server, callback=None):
        return await self._result("deploy_cs2_server", callback)

    async def start_server(self, server, callback=None):
        return await self._result("start_server", callback)

    async def stop_server(self, server):
        return await self._result("stop_server")

    async def get_server_status(self, _server):
        self.calls.append("get_server_status")
        return self.success, self.status if self.success else "status unavailable"

    async def update_server(self, server, callback=None):
        return await self._result("update_server", callback)

    async def validate_server(self, server, callback=None):
        return await self._result("validate_server", callback)

    async def install_metamod(self, server, callback=None):
        return await self._result("install_metamod", callback)

    async def update_metamod(self, server, callback=None):
        return await self._result("update_metamod", callback)

    async def install_counterstrikesharp(self, server, callback=None):
        return await self._result("install_counterstrikesharp", callback)

    async def update_counterstrikesharp(self, server, callback=None):
        return await self._result("update_counterstrikesharp", callback)

    async def install_cs2fixes(self, server, callback=None):
        return await self._result("install_cs2fixes", callback)

    async def update_cs2fixes(self, server, callback=None):
        return await self._result("update_cs2fixes", callback)

    async def install_swiftly(self, server, callback=None):
        return await self._result("install_swiftly", callback)

    async def update_swiftly(self, server, callback=None):
        return await self._result("update_swiftly", callback)

    async def backup_plugins(self, server, callback=None):
        return await self._result("backup_plugins", callback)


async def _no_op(*_args, **_kwargs):
    return None


def _patch_common(monkeypatch, manager, *, locked_server=None):
    server = locked_server or _server()
    monkeypatch.setattr(
        deployment, "get_server_and_verify_ownership", AsyncMock(return_value=server)
    )
    monkeypatch.setattr(deployment, "SSHManager", lambda: manager)
    monkeypatch.setattr(deployment.redis_manager, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(deployment.redis_manager, "set", AsyncMock(return_value=True))
    monkeypatch.setattr(deployment.redis_manager, "delete", AsyncMock(return_value=True))
    monkeypatch.setattr(deployment.redis_manager, "clear_deployment_progress", _no_op)
    monkeypatch.setattr(deployment.redis_manager, "set_server_status", _no_op)
    monkeypatch.setattr(deployment, "record_audit_event", _no_op)
    monkeypatch.setattr(deployment, "send_deployment_update", _no_op)
    monkeypatch.setattr(deployment, "send_discord_action_notification", _no_op)
    monkeypatch.setattr(deployment, "clear_deployment_progress_after_delay", _no_op)
    monkeypatch.setattr(deployment, "_store_task", lambda task: task.cancel())
    monkeypatch.setattr(deployment.server_monitor, "reset_restart_history", lambda _id: None)
    return server


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        "deploy",
        "start",
        "stop",
        "update",
        "validate",
        "install_metamod",
        "update_metamod",
        "install_counterstrikesharp",
        "update_counterstrikesharp",
        "install_cs2fixes",
        "update_cs2fixes",
        "install_swiftly",
        "update_swiftly",
        "backup_plugins",
    ],
)
async def test_execute_server_action_success_branches(monkeypatch, action):
    manager = _SSH()
    server = _patch_common(monkeypatch, manager)
    if action == "backup_plugins":
        monkeypatch.setattr(
            deployment,
            "upload_latest_plugin_backup_to_s3",
            AsyncMock(return_value=(True, "uploaded")),
        )

    framework = AsyncMock()
    known_github = AsyncMock()
    monkeypatch.setattr(
        "services.plugin_auto_update_service.record_framework_installation", framework
    )
    monkeypatch.setattr(
        "services.plugin_auto_update_service.record_known_github_installation", known_github
    )

    response = await deployment.execute_server_action(
        server.id,
        ServerAction(action=action),
        _DB(),
        SimpleNamespace(id=server.user_id, is_admin=False),
        server,
    )

    assert response.success is True
    assert manager.calls
    if action == "install_cs2fixes" or action == "update_cs2fixes":
        known_github.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_text", ["running", "stopped", "unknown"])
async def test_execute_server_action_status_branches(monkeypatch, status_text):
    manager = _SSH(status=status_text)
    server = _patch_common(monkeypatch, manager)
    response = await deployment.execute_server_action(
        server.id,
        ServerAction(action="status"),
        _DB(),
        SimpleNamespace(id=server.user_id),
        server,
    )
    assert response.success is True
    assert response.data["status"] == status_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action", ["deploy", "start", "stop", "update", "validate", "install_metamod"]
)
async def test_execute_server_action_failure_branches(monkeypatch, action):
    manager = _SSH(success=False)
    server = _patch_common(monkeypatch, manager)
    response = await deployment.execute_server_action(
        server.id,
        ServerAction(action=action),
        _DB(),
        SimpleNamespace(id=server.user_id),
        server,
    )
    assert response.success is False
    assert "message" in response.model_dump()


@pytest.mark.asyncio
async def test_action_preflight_lock_and_exception_paths(monkeypatch):
    server = _server()
    db = _DB()
    monkeypatch.setattr(
        deployment, "get_server_and_verify_ownership", AsyncMock(return_value=server)
    )
    monkeypatch.setattr(deployment.redis_manager, "get", AsyncMock(return_value="locked"))
    with pytest.raises(HTTPException) as conflict:
        await deployment.execute_server_action(
            server.id, ServerAction(action="start"), db, SimpleNamespace(id=7), None
        )
    assert conflict.value.status_code == 409

    manager = _SSH(raise_on="start_server")
    _patch_common(monkeypatch, manager, locked_server=server)
    with pytest.raises(HTTPException) as failed:
        await deployment.execute_server_action(
            server.id, ServerAction(action="start"), db, SimpleNamespace(id=7), server
        )
    assert failed.value.status_code == 500
    assert server.status == ServerStatus.ERROR

    manager = _SSH()
    _patch_common(monkeypatch, manager, locked_server=server)
    with pytest.raises(HTTPException) as invalid:
        await deployment.execute_server_action(
            server.id, SimpleNamespace(action="not-real"), db, SimpleNamespace(id=7), server
        )
    assert invalid.value.status_code == 500


@pytest.mark.asyncio
async def test_restart_preflight_and_cleanup_variants(monkeypatch):
    server = _server(auto_clear_crash_hours=1, last_status_check=None)
    manager = _SSH()
    _patch_common(monkeypatch, manager, locked_server=server)
    monkeypatch.setattr(deployment.asyncio, "sleep", _no_op)
    response = await deployment.execute_server_action(
        server.id, ServerAction(action="restart"), _DB(), SimpleNamespace(id=7), server
    )
    assert response.success is True
    assert "start_server" in manager.calls

    manager = _SSH()
    manager.check_session_manager_available = AsyncMock(return_value=(False, "tmux unavailable"))
    _patch_common(monkeypatch, manager, locked_server=_server())
    response = await deployment.execute_server_action(
        301, ServerAction(action="restart"), _DB(), SimpleNamespace(id=7), manager and _server()
    )
    assert response.success is False
    assert "left untouched" in response.message


@pytest.mark.asyncio
async def test_deployment_lock_progress_logs_and_cancel_paths(monkeypatch):
    server = _server(status=ServerStatus.DEPLOYING)
    db = _DB()
    monkeypatch.setattr(
        deployment, "get_server_and_verify_ownership", AsyncMock(return_value=server)
    )
    monkeypatch.setattr(deployment.redis_manager, "get", AsyncMock(return_value="1"))
    result = await deployment.check_deployment_lock(server.id, db, SimpleNamespace(id=7))
    assert result.body and b"lock_exists" in result.body

    monkeypatch.setattr(
        deployment.redis_manager, "get_deployment_progress", AsyncMock(return_value=[{"m": 1}])
    )
    progress = await deployment.get_deployment_progress(server.id, db, SimpleNamespace(id=7))
    assert progress["total_messages"] == 1

    monkeypatch.setattr(deployment.DeploymentLog, "get_logs_by_server", AsyncMock(return_value=[]))
    assert (
        await deployment.get_server_logs(server.id, db=db, current_user=SimpleNamespace(id=7)) == []
    )

    monkeypatch.setattr(deployment, "request_steamcmd_cancel", _no_op)
    monkeypatch.setattr(deployment, "force_clear_steamcmd_lock", _no_op)
    monkeypatch.setattr(deployment, "clear_steamcmd_cancel", _no_op)
    monkeypatch.setattr(deployment.maintenance_lock_service, "force_release_server_lock", _no_op)
    monkeypatch.setattr(deployment.redis_manager, "clear_deployment_progress", _no_op)
    monkeypatch.setattr(
        deployment,
        "SSHManager",
        lambda: SimpleNamespace(
            connect=AsyncMock(return_value=(False, "offline")), disconnect=AsyncMock()
        ),
    )
    monkeypatch.setattr(
        "services.server_operation_hub.server_operation_hub.abort", AsyncMock(return_value=False)
    )
    cancelled = await deployment.cancel_deployment(server.id, db, SimpleNamespace(id=7))
    assert cancelled.body and b"force-stopped" in cancelled.body
    assert server.status == ServerStatus.ERROR
