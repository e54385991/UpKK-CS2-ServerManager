"""Regression tests for CS2 executable checks and deployment retries."""

import pytest

from modules.models import AuthType, Server, ServerStatus
from services.ssh_manager import SSHManager


def server_fixture() -> Server:
    return Server(
        id=91,
        user_id=1,
        name="Executable guard regression",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        session_manager="tmux",
        status=ServerStatus.RUNNING,
        game_directory="/srv/cs2-panel",
    )


class MissingExecutableManager(SSHManager):
    def __init__(self):
        super().__init__()
        self.commands = []
        self.disconnect_calls = 0
        self.manager_check_called = False
        self.stop_called = False

    async def connect(self, server):
        return True, "connected"

    async def disconnect(self):
        self.disconnect_calls += 1

    async def execute_command(self, command, *args, **kwargs):
        self.commands.append(command)
        return False, "", "not found"

    async def _configured_session_manager_available_connected(self, *args, **kwargs):
        self.manager_check_called = True
        return True, "available"

    async def _stop_server_sessions_connected(self, *args, **kwargs):
        self.stop_called = True
        return True, []


@pytest.mark.asyncio
async def test_start_aborts_before_session_cleanup_when_executable_is_missing():
    server = server_fixture()
    manager = MissingExecutableManager()

    success, message = await manager.start_server(server)

    expected_path = "/srv/cs2-panel/cs2/game/bin/linuxsteamrt64/cs2"
    assert success is False
    assert expected_path in message
    assert "重新部署" in message
    assert "修复" in message
    assert manager.manager_check_called is False
    assert manager.stop_called is False
    assert manager.disconnect_calls == 1
    assert manager.commands == [f"test -f {expected_path} && echo exists"]


@pytest.mark.asyncio
async def test_restart_preflight_checks_executable_before_session_manager():
    server = server_fixture()
    manager = MissingExecutableManager()

    success, message = await manager.check_session_manager_available(server)

    assert success is False
    assert "可执行文件不存在" in message
    assert manager.manager_check_called is False
    assert manager.disconnect_calls == 1


class SteamCMDRetryProbe(SSHManager):
    STEAMCMD_RETRY_DELAY = 0

    def __init__(self, command_results):
        super().__init__()
        self.command_results = list(command_results)
        self.command_calls = 0
        self.kill_calls = 0

    async def execute_command_streaming(self, *args, **kwargs):
        self.command_calls += 1
        return self.command_results[
            min(
                self.command_calls - 1,
                len(self.command_results) - 1,
            )
        ]

    async def _kill_steamcmd_processes(self, *args, **kwargs):
        self.kill_calls += 1


@pytest.mark.asyncio
async def test_deployment_artifact_missing_forces_exactly_five_retries():
    server = server_fixture()
    manager = SteamCMDRetryProbe([(True, "Success!", "")])
    verification_calls = 0

    async def executable_still_missing():
        nonlocal verification_calls
        verification_calls += 1
        return False

    success, _, stderr = await manager._execute_steamcmd_with_retry(
        "steamcmd install",
        server,
        max_retries=5,
        completion_check=executable_still_missing,
    )

    assert success is False
    assert manager.command_calls == 6  # initial attempt plus five retries
    assert verification_calls == 6
    assert manager.kill_calls == 5
    assert "Required deployment file is missing" in stderr


@pytest.mark.asyncio
async def test_deployment_accepts_verified_file_after_interrupted_command():
    server = server_fixture()
    manager = SteamCMDRetryProbe([(False, "", "SteamCMD interrupted without a retryable keyword")])
    verification_results = iter([False, False, True])

    async def executable_appears_on_third_attempt():
        return next(verification_results)

    success, _, _ = await manager._execute_steamcmd_with_retry(
        "steamcmd install",
        server,
        max_retries=5,
        completion_check=executable_appears_on_third_attempt,
    )

    assert success is True
    assert manager.command_calls == 3
    assert manager.kill_calls == 2
