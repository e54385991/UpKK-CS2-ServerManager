"""覆盖 SteamCMD 更新/验证流程的会话、恢复和非关键缓存失败分支。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.ssh.game_update import GameUpdateMixin


class _Update(GameUpdateMixin):
    def __init__(self):
        self.conn = None
        self.connect = AsyncMock(return_value=(True, "connected"))
        self.disconnect = AsyncMock()
        self._prepare_update_session = AsyncMock(return_value=(False, None))
        self._kill_stray_cs2_processes = AsyncMock()
        self._kill_steamcmd_processes = AsyncMock()
        self._running_server_session_managers = AsyncMock(return_value=[])
        self._configured_session_manager_available_connected = AsyncMock(return_value=(True, "ok"))
        self._stop_server_sessions_connected = AsyncMock(return_value=(True, ""))
        self._execute_steamcmd_with_retry = AsyncMock(return_value=(True, "updated", ""))
        self.start_server = AsyncMock(return_value=(True, "started"))


def _server(**overrides):
    values = dict(id=3, user_id=7, game_directory="/srv/cs2")
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_update_server_success_stopped_and_running_restore(monkeypatch):
    manager = _Update()
    cache = AsyncMock(return_value=(True, "1.2.3"))
    monkeypatch.setattr("services.steam_inf_service.steam_inf_service.refresh_version_cache", cache)
    monkeypatch.setattr("services.ssh.game_update.resolve_steamcmd_max_retries", AsyncMock(return_value=2))
    progress = []
    assert await manager.update_server(_server(), progress.append) == (
        True,
        "Server updated successfully; server remained stopped",
    )
    assert any("SteamCMD Update Command" in item for item in progress)
    cache.assert_awaited_once()
    manager.disconnect.assert_awaited_once()

    manager = _Update()
    manager._prepare_update_session.return_value = (True, None)
    monkeypatch.setattr("services.steam_inf_service.steam_inf_service.refresh_version_cache", AsyncMock(return_value=(False, None)))
    assert await manager.update_server(_server()) == (
        True,
        "Server updated and restored to running state successfully",
    )
    manager.start_server.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_server_failure_preparation_retry_and_cache_exception(monkeypatch):
    manager = _Update()
    manager.connect.return_value = (False, "offline")
    assert await manager.update_server(_server()) == (False, "Connection failed: offline")

    manager = _Update()
    manager._prepare_update_session.return_value = (True, "session busy")
    assert await manager.update_server(_server()) == (False, "session busy")

    manager = _Update()
    manager._prepare_update_session.return_value = (True, None)
    manager._execute_steamcmd_with_retry.return_value = (False, "stdout error", "stderr error")
    manager.start_server.return_value = (False, "restart failed")
    monkeypatch.setattr("services.ssh.game_update.resolve_steamcmd_max_retries", AsyncMock(return_value=1))
    monkeypatch.setattr("services.steam_inf_service.steam_inf_service.refresh_version_cache", AsyncMock(side_effect=RuntimeError("cache down")))
    result = await manager.update_server(_server())
    assert result[0] is False
    assert "recovery start failed" in result[1]
    assert "stderr error" in result[1]


@pytest.mark.asyncio
async def test_validate_server_warning_restart_and_error_paths(monkeypatch):
    manager = _Update()
    manager._running_server_session_managers.return_value = ["tmux"]
    manager._execute_steamcmd_with_retry.return_value = (False, "", "validation error")
    manager.start_server.return_value = (False, "cannot restart")
    monkeypatch.setattr("services.ssh.game_update.resolve_steamcmd_max_retries", AsyncMock(return_value=1))
    monkeypatch.setattr("services.steam_inf_service.steam_inf_service.refresh_version_cache", AsyncMock(return_value=(True, "v")))
    result = await manager.validate_server(_server())
    assert result == (True, "Server updated and validated successfully")
    assert manager._stop_server_sessions_connected.await_count == 1
    assert manager.start_server.await_count == 1

    manager = _Update()
    manager._running_server_session_managers.return_value = ["screen"]
    manager._configured_session_manager_available_connected.return_value = (False, "missing tool")
    assert (await manager.validate_server(_server()))[0] is False

    manager = _Update()
    manager._execute_steamcmd_with_retry.side_effect = RuntimeError("boom")
    result = await manager.validate_server(_server())
    assert result == (False, "Validation error: boom")


@pytest.mark.asyncio
async def test_prepare_restore_and_status_helpers_cover_stops_and_offline():
    manager = _Update()
    manager._prepare_update_session = GameUpdateMixin._prepare_update_session.__get__(manager)
    server = _server()
    send = AsyncMock()
    manager._kill_steamcmd_processes = AsyncMock()
    manager._running_server_session_managers.return_value = []
    assert await manager._prepare_update_session(server, send, None) == (False, None)
    manager._running_server_session_managers.return_value = ["tmux", "screen"]
    manager._configured_session_manager_available_connected.return_value = (False, "not configured")
    assert (await manager._prepare_update_session(server, send, None))[1].startswith("Server update aborted")
    manager._configured_session_manager_available_connected.return_value = (True, "ok")
    manager._stop_server_sessions_connected.return_value = (False, "failed")
    assert (await manager._prepare_update_session(server, send, None))[1].startswith("Server update aborted")

    assert await manager._restore_updated_server(server, False, None, send) == (
        True,
        "Server updated successfully; server remained stopped",
    )
    manager.start_server.return_value = (False, "bad")
    assert (await manager._restore_updated_server(server, True, None, send))[0] is False
    manager.start_server.return_value = (True, "ok")
    assert (await manager._restore_updated_server(server, True, None, send))[0] is True

    manager.connect.return_value = (False, "offline")
    assert await manager.get_server_status(server) == (False, "offline")
    manager.connect.return_value = (True, "ok")
    manager._running_server_session_managers.return_value = ["tmux"]
    assert await manager.get_server_status(server) == (True, "running")
    manager._running_server_session_managers.return_value = []
    assert await manager.get_server_status(server) == (True, "stopped")
    manager._running_server_session_managers.side_effect = RuntimeError("query")
    assert await manager.get_server_status(server) == (False, "unknown")
