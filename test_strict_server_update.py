"""Regression tests for SteamCMD and restart failures being reported as failures."""
import pytest

from modules.models import AuthType, Server
from services.ssh_manager import SSHManager


def server_fixture():
    return Server(
        id=9, user_id=1, name="Update Test", host="127.0.0.1",
        ssh_user="steam", auth_type=AuthType.PASSWORD,
    )


class FakeUpdateManager(SSHManager):
    def __init__(self, *, running=False, steam_success=True, restart_success=True):
        super().__init__()
        self.running = running
        self.steam_success = steam_success
        self.restart_success = restart_success
        self.session_checks = 0

    async def connect(self, server):
        return True, "connected"

    async def disconnect(self):
        return None

    async def _kill_steamcmd_processes(self, *args, **kwargs):
        return None

    async def _kill_stray_cs2_processes(self, *args, **kwargs):
        return None

    async def execute_command(self, command, *args, **kwargs):
        is_session_status = (
            "screen -list" in command
            or "has-session" in command
        )
        if is_session_status:
            self.session_checks += 1
            if self.running and self.session_checks == 1:
                return True, "cs2server_9", ""
            return False, "", ""
        return True, "", ""

    async def _execute_steamcmd_with_retry(self, *args, **kwargs):
        if self.steam_success:
            return True, "Success! App '730' fully installed.", ""
        return False, "Connection closed", "network unavailable"

    async def start_server(self, server, progress_callback=None):
        return self.restart_success, "restart result"


@pytest.mark.asyncio
async def test_steamcmd_failure_without_word_error_is_failure(monkeypatch):
    async def no_sleep(*args, **kwargs):
        return None
    monkeypatch.setattr("services.ssh_manager.asyncio.sleep", no_sleep)
    manager = FakeUpdateManager(steam_success=False)
    success, message = await manager.update_server(server_fixture())
    assert success is False
    assert "network unavailable" in message


@pytest.mark.asyncio
async def test_restart_failure_makes_update_fail(monkeypatch):
    async def no_sleep(*args, **kwargs):
        return None
    async def version_ok(*args, **kwargs):
        return True, "1.2.3.4"
    monkeypatch.setattr("services.ssh_manager.asyncio.sleep", no_sleep)
    monkeypatch.setattr("services.steam_inf_service.steam_inf_service.refresh_version_cache", version_ok)
    manager = FakeUpdateManager(running=True, steam_success=True, restart_success=False)
    success, message = await manager.update_server(server_fixture())
    assert success is False
    assert "failed to restore" in message
