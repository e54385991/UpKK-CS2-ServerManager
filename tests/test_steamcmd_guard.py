"""Force-stop cancel tokens must not poison the next SteamCMD deploy."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes.actions import deployment as deployment_routes
from modules.models import AuthType, Server, ServerStatus
from modules.schemas import ServerAction
from services.ssh_manager import SSHManager
from services.steamcmd_guard import (
    STEAMCMD_FORCE_TERMINATED,
    clear_steamcmd_cancel,
    cs2_deploy_steamcmd_failure_message,
    is_steamcmd_force_terminated,
    prepare_steamcmd_operation,
    request_steamcmd_cancel,
    steamcmd_cancel_requested,
)


def _server(*, server_id: int = 2) -> Server:
    return Server(
        id=server_id,
        user_id=1,
        name="lan-ops",
        host="192.168.50.143",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        session_manager="tmux",
        status=ServerStatus.ERROR,
        game_directory="/tmp/cs2-lan-ops",
    )


class _FakeDB:
    def __init__(self):
        self.added = []
        self.commit_calls = 0
        self.refresh_calls = []

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commit_calls += 1

    async def refresh(self, value):
        self.refresh_calls.append(value)


class _DeployProbe:
    def __init__(self):
        self.deploy_calls = 0

    async def deploy_cs2_server(self, server, progress_callback=None):
        self.deploy_calls += 1
        return True, "deployed"

    async def disconnect(self):
        return None


class _SteamCMDRetryProbe(SSHManager):
    STEAMCMD_RETRY_DELAY = 0

    def __init__(self, command_results):
        super().__init__()
        self.command_results = list(command_results)
        self.command_calls = 0
        self.kill_calls = 0

    async def execute_command_streaming(self, *args, **kwargs):
        self.command_calls += 1
        return self.command_results[min(self.command_calls - 1, len(self.command_results) - 1)]

    async def _list_steamcmd_pids(self, *args, **kwargs):
        return []

    async def _steamcmd_session_manager(self, server):
        return None

    async def _steamcmd_session_running(self, server, manager=None):
        return None

    async def _kill_steamcmd_processes(self, *args, **kwargs):
        self.kill_calls += 1


@pytest.mark.asyncio
async def test_prepare_clears_leftover_force_stop_token(monkeypatch):
    store: dict[str, str] = {}

    async def fake_set(key, value, expire=None):
        store[key] = value
        return True

    async def fake_get(key):
        return store.get(key)

    async def fake_delete(key):
        store.pop(key, None)
        return True

    monkeypatch.setattr("services.steamcmd_guard.redis_manager.set", fake_set)
    monkeypatch.setattr("services.steamcmd_guard.redis_manager.get", fake_get)
    monkeypatch.setattr("services.steamcmd_guard.redis_manager.delete", fake_delete)

    await request_steamcmd_cancel(2)
    assert await steamcmd_cancel_requested(2) is True
    await prepare_steamcmd_operation(2)
    assert await steamcmd_cancel_requested(2) is False
    await request_steamcmd_cancel(2)
    await clear_steamcmd_cancel(2)
    assert await steamcmd_cancel_requested(2) is False


def test_operator_force_stop_is_not_exhausted_retry_alarm():
    path = "/tmp/cs2-lan-ops/cs2/game/bin/linuxsteamrt64/cs2"
    message = cs2_deploy_steamcmd_failure_message(
        max_retries=20,
        executable_path=path,
        error_detail=STEAMCMD_FORCE_TERMINATED,
    )
    assert is_steamcmd_force_terminated(STEAMCMD_FORCE_TERMINATED)
    assert "20" not in message
    assert "自动恢复" not in message
    assert path not in message
    assert "Force-terminated by operator" not in message
    assert "可以重新部署" in message
    assert "You can deploy again" in message


def test_missing_binary_after_real_retries_still_alarms():
    path = "/tmp/cs2-lan-ops/cs2/game/bin/linuxsteamrt64/cs2"
    message = cs2_deploy_steamcmd_failure_message(
        max_retries=20,
        executable_path=path,
        error_detail="Required deployment file is missing after SteamCMD exit",
    )
    assert "20" in message
    assert "自动恢复" in message
    assert path in message


@pytest.mark.asyncio
async def test_stale_force_stop_flag_aborts_steamcmd_before_prepare(monkeypatch):
    server = _server()
    manager = _SteamCMDRetryProbe([(True, "Success!", "")])

    async def always_cancelled(_server_id):
        return True

    monkeypatch.setattr("services.ssh.game.steamcmd_cancel_requested", always_cancelled)

    success, _, stderr = await manager._execute_steamcmd_with_retry(
        "steamcmd install",
        server,
        max_retries=20,
    )

    assert success is False
    assert stderr == STEAMCMD_FORCE_TERMINATED
    assert manager.command_calls == 0


@pytest.mark.asyncio
async def test_force_stop_then_prepare_starts_fresh_steamcmd(monkeypatch):
    server = _server()
    manager = _SteamCMDRetryProbe([(True, "Success!", "")])
    cancelled = {"on": True}

    async def fake_requested(_server_id):
        return cancelled["on"]

    monkeypatch.setattr("services.ssh.game.steamcmd_cancel_requested", fake_requested)

    success, _, stderr = await manager._execute_steamcmd_with_retry(
        "steamcmd install",
        server,
        max_retries=20,
    )
    assert success is False
    assert stderr == STEAMCMD_FORCE_TERMINATED
    assert manager.command_calls == 0
    alarm = cs2_deploy_steamcmd_failure_message(
        max_retries=20,
        executable_path="/tmp/cs2-lan-ops/cs2/game/bin/linuxsteamrt64/cs2",
        error_detail=stderr,
    )
    assert "自动恢复" not in alarm

    cancelled["on"] = False

    retry = _SteamCMDRetryProbe([(True, "Success!", "")])
    success, _, stderr = await retry._execute_steamcmd_with_retry(
        "steamcmd install",
        server,
        max_retries=20,
    )
    assert success is True
    assert retry.command_calls == 1
    assert STEAMCMD_FORCE_TERMINATED not in stderr


@pytest.mark.asyncio
async def test_cancel_deployment_releases_cancel_token(monkeypatch):
    server = _server()
    server.status = ServerStatus.DEPLOYING
    requested: list[int] = []
    cleared: list[str] = []

    async def fake_request(server_id):
        requested.append(server_id)

    async def fake_clear_cancel(server_id):
        cleared.append(f"cancel:{server_id}")

    async def fake_clear_lock(server_id):
        cleared.append(f"lock:{server_id}")

    monkeypatch.setattr(
        deployment_routes,
        "get_server_and_verify_ownership",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(deployment_routes, "request_steamcmd_cancel", fake_request)
    monkeypatch.setattr(deployment_routes, "clear_steamcmd_cancel", fake_clear_cancel)
    monkeypatch.setattr(deployment_routes, "force_clear_steamcmd_lock", fake_clear_lock)
    monkeypatch.setattr(
        "services.server_operation_hub.server_operation_hub.abort",
        AsyncMock(return_value={"operation_id": "op-1"}),
    )

    class OfflineSSH:
        async def connect(self, _server):
            return False, "offline"

        async def disconnect(self):
            return None

    monkeypatch.setattr(deployment_routes, "SSHManager", lambda: OfflineSSH())
    monkeypatch.setattr(
        deployment_routes.redis_manager,
        "clear_deployment_progress",
        AsyncMock(),
    )
    release_maintenance = AsyncMock(return_value=True)
    monkeypatch.setattr(
        deployment_routes.maintenance_lock_service,
        "force_release_server_lock",
        release_maintenance,
    )

    response = await deployment_routes.cancel_deployment(
        server.id,
        _FakeDB(),
        SimpleNamespace(id=1, is_admin=False),
    )

    assert response.status_code == 200
    assert requested == [server.id]
    assert f"cancel:{server.id}" in cleared
    assert f"lock:{server.id}" in cleared
    release_maintenance.assert_awaited()
    assert server.status == ServerStatus.ERROR


@pytest.mark.asyncio
async def test_new_deploy_clears_stale_cancel_before_steamcmd(monkeypatch):
    server = _server()
    manager = _DeployProbe()
    prepared: list[int] = []

    async def fake_prepare(server_id):
        prepared.append(server_id)

    async def no_lock(*_args, **_kwargs):
        return None

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        deployment_routes,
        "get_server_and_verify_ownership",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(deployment_routes, "prepare_steamcmd_operation", fake_prepare)
    monkeypatch.setattr(deployment_routes, "SSHManager", lambda: manager)
    monkeypatch.setattr(deployment_routes.redis_manager, "get", no_lock)
    monkeypatch.setattr(deployment_routes.redis_manager, "set", no_op)
    monkeypatch.setattr(deployment_routes.redis_manager, "delete", no_op)
    monkeypatch.setattr(deployment_routes.redis_manager, "clear_deployment_progress", no_op)
    monkeypatch.setattr(deployment_routes.redis_manager, "set_server_status", no_op)
    monkeypatch.setattr(deployment_routes, "record_audit_event", no_op)
    monkeypatch.setattr(deployment_routes, "send_deployment_update", no_op)
    monkeypatch.setattr(deployment_routes, "send_discord_action_notification", no_op)
    monkeypatch.setattr(deployment_routes, "_store_task", lambda _task: None)

    response = await deployment_routes.server_action(
        server.id,
        ServerAction(action="deploy"),
        _FakeDB(),
        SimpleNamespace(id=server.user_id, is_admin=False),
        server,
        SimpleNamespace(client=SimpleNamespace(host="test"), headers={}),
    )

    assert prepared == [server.id]
    assert manager.deploy_calls == 1
    assert response.success is True
