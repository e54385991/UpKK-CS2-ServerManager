"""覆盖 SteamCMD 会话管理器的回退、停止、重试和清理分支。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from services.ssh import game_steamcmd as steamcmd_module
from services.ssh.game_steamcmd import GameSteamcmdMixin


def _server(**overrides):
    values = {
        "id": 3,
        "user_id": 7,
        "game_directory": "/srv/cs2",
        "session_manager": "tmux",
        "game_port": 27015,
    }
    values.update(overrides)
    return type("Server", (), values)()


class _Manager(GameSteamcmdMixin):
    def __init__(self, results=()):
        self.results = list(results)
        self.commands = []

    async def execute_command(self, command, **_kwargs):
        self.commands.append(command)
        if self.results:
            value = self.results.pop(0)
            return value() if callable(value) else value
        return True, "", ""


@pytest.mark.asyncio
async def test_stop_session_manager_and_progress_variants(monkeypatch):
    manager = _Manager()
    server = _server()
    manager.connect = AsyncMock(return_value=(False, "offline"))
    assert await manager.stop_server(server) == (False, "Connection failed: offline")

    manager.connect = AsyncMock(return_value=(True, "ok"))
    manager.disconnect = AsyncMock()
    manager._stop_server_sessions_connected = AsyncMock(return_value=(True, []))
    manager._kill_stray_cs2_processes = AsyncMock()
    assert await manager.stop_server(server) == (
        True,
        "Server is not running (no screen/tmux session found)",
    )
    manager._stop_server_sessions_connected.return_value = (False, ["tmux", "screen"])
    assert (await manager.stop_server(server))[0] is False
    manager._stop_server_sessions_connected.side_effect = RuntimeError("stop crash")
    assert "Stop error" in (await manager.stop_server(server))[1]

    async_progress = AsyncMock()
    await manager._send_progress_if_callback(async_progress, "async")
    sync_progress = []
    await manager._send_progress_if_callback(sync_progress.append, "sync")
    await manager._send_progress_if_callback(None, "ignored")
    assert sync_progress == ["sync"]

    manager = _Manager([(False, "", ""), (True, "", "")])
    assert await manager._steamcmd_session_manager(_server(session_manager="screen")) == "tmux"
    manager = _Manager([(False, "", ""), (False, "", "")])
    assert await manager._steamcmd_session_manager(server) is None
    manager = _Manager([(True, "0\n", "")])
    assert await manager._read_steamcmd_exit_code(server) == 0
    manager = _Manager([(False, "", "offline")])
    assert await manager._read_steamcmd_exit_code(server) is None


@pytest.mark.asyncio
async def test_start_session_and_streaming_fallbacks(monkeypatch):
    server = _server()
    manager = _Manager()
    progress = AsyncMock()
    monkeypatch.setattr(
        "services.ssh.game_steamcmd._legacy_cancel_requested", AsyncMock(return_value=False)
    )
    manager._steamcmd_session_running = AsyncMock(return_value="tmux")
    assert (
        await manager._start_steamcmd_session("cmd", server, "tmux", "name", "/tmp/e", progress)
    )[0]

    manager._steamcmd_session_running = AsyncMock(side_effect=[None, None])
    manager.execute_command = AsyncMock(return_value=(False, "", "failed"))
    result = await manager._start_steamcmd_session(
        "cmd", server, "tmux", "name", "/tmp/e", progress
    )
    assert result == (False, "", "failed")

    manager.execute_command = AsyncMock(
        side_effect=[(True, "", ""), (True, "", ""), (True, "", "")]
    )
    manager._steamcmd_session_running = AsyncMock(side_effect=[None, None])
    manager._read_steamcmd_exit_code = AsyncMock(return_value=2)
    result = await manager._start_steamcmd_session(
        "cmd", server, "tmux", "name", "/tmp/e", progress
    )
    assert result[0] is False and "exited 2" in result[2]

    manager.execute_command = AsyncMock(side_effect=[(True, "", ""), (True, "", "")])
    manager._steamcmd_session_running = AsyncMock(side_effect=[None, None])
    manager._read_steamcmd_exit_code = AsyncMock(return_value=None)
    result = await manager._start_steamcmd_session(
        "cmd", server, "tmux", "name", "/tmp/e", progress
    )
    assert result[0] is False and "did not start" in result[2]

    manager._steamcmd_session_manager = AsyncMock(return_value=None)
    manager.execute_command_streaming = AsyncMock(return_value=(True, "direct", ""))
    result = await manager._stream_steamcmd_with_heartbeat("cmd", server, progress, 10)
    assert result == (True, "direct", "")

    manager._steamcmd_session_manager = AsyncMock(return_value="tmux")
    manager._start_steamcmd_session = AsyncMock(return_value=(False, "", "startup"))
    assert (await manager._stream_steamcmd_with_heartbeat("cmd", server, progress, 10))[
        2
    ] == "startup"

    manager._start_steamcmd_session = AsyncMock(return_value=(True, "", ""))
    manager._steamcmd_session_running = AsyncMock(return_value=None)
    manager.execute_command = AsyncMock(return_value=(True, "first\nsecond", ""))
    manager._read_steamcmd_exit_code = AsyncMock(return_value=0)
    monkeypatch.setattr(steamcmd_module.time, "monotonic", MockMonotonic([0, 1]))
    result = await manager._stream_steamcmd_with_heartbeat("cmd", server, progress, 10)
    assert result[:2] == (True, "second")

    manager._steamcmd_session_running = AsyncMock(return_value=None)
    manager._read_steamcmd_exit_code = AsyncMock(return_value=None)
    manager._list_steamcmd_pids = AsyncMock(return_value=[])
    monkeypatch.setattr(steamcmd_module.time, "monotonic", MockMonotonic([0, 1]))
    result = await manager._stream_steamcmd_with_heartbeat("cmd", server, progress, 10)
    assert "unexpectedly" in result[2]

    manager._steamcmd_session_running = AsyncMock(return_value="tmux")
    manager._list_steamcmd_pids = AsyncMock(return_value=["1"])
    manager.execute_command = AsyncMock(return_value=(True, "heartbeat line", ""))
    monkeypatch.setattr(steamcmd_module.time, "monotonic", MockMonotonic([0, 1, 30, 30, 61]))
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    result = await manager._stream_steamcmd_with_heartbeat("cmd", server, progress, 10)
    assert result[0] is False and result[2] == "Command timeout"

    manager._steamcmd_session_running = AsyncMock(side_effect=RuntimeError("hiccup"))
    monkeypatch.setattr(steamcmd_module.time, "monotonic", MockMonotonic([0, 1, 61]))
    result = await manager._stream_steamcmd_with_heartbeat("cmd", server, progress, 10)
    assert result[2] == "Command timeout"

    monkeypatch.setattr(
        "services.ssh.game_steamcmd._legacy_cancel_requested", AsyncMock(return_value=True)
    )
    manager._kill_steamcmd_processes = AsyncMock()
    monkeypatch.setattr(steamcmd_module.time, "monotonic", MockMonotonic([0, 1]))
    result = await manager._stream_steamcmd_with_heartbeat("cmd", server, progress, 10)
    assert result[2]


@pytest.mark.asyncio
async def test_retry_helpers_completion_and_process_cleanup(monkeypatch):
    server = _server()
    manager = _Manager()
    manager.STEAMCMD_RETRY_DELAY = 0
    send = AsyncMock()
    callback = AsyncMock()
    monkeypatch.setattr(
        "services.ssh.game_steamcmd._legacy_cancel_requested", AsyncMock(return_value=False)
    )
    manager._steamcmd_session_running = AsyncMock(return_value="tmux")
    assert await manager._prepare_steamcmd_retry(server, 1, 2, send, callback)
    manager._steamcmd_session_running = AsyncMock(return_value=None)
    manager._list_steamcmd_pids = AsyncMock(return_value=[])
    manager._kill_steamcmd_processes = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    assert await manager._prepare_steamcmd_retry(server, 1, 2, send, callback)
    monkeypatch.setattr(
        "services.ssh.game_steamcmd._legacy_cancel_requested", AsyncMock(return_value=True)
    )
    assert not await manager._prepare_steamcmd_retry(server, 1, 2, send, callback)
    assert await manager._prepare_steamcmd_retry(server, 0, 2, send, callback)

    manager._stream_steamcmd_with_heartbeat = AsyncMock(return_value=(True, "out", "err"))
    assert await manager._run_steamcmd_attempt("cmd", server, send, 10) == (
        True,
        "out",
        "err",
        "",
        False,
    )
    manager._stream_steamcmd_with_heartbeat = AsyncMock(return_value=(False, "out", "fatal"))
    failed = await manager._run_steamcmd_attempt("cmd", server, send, 10)
    assert failed[0] is False and failed[4] is True
    manager._stream_steamcmd_with_heartbeat = AsyncMock(side_effect=asyncio.TimeoutError())
    assert (await manager._run_steamcmd_attempt("cmd", server, send, 10))[2] == "Command timeout"
    manager._stream_steamcmd_with_heartbeat = AsyncMock(side_effect=RuntimeError("boom"))
    assert "Unexpected" in (await manager._run_steamcmd_attempt("cmd", server, send, 10))[3]

    assert (
        await manager._process_steamcmd_completion(0, 2, send, False, "", "bad", "bad", True, True)
    )[0] is True
    verified_false = await manager._process_steamcmd_completion(
        0, 2, send, True, "", "", "", False, False
    )
    assert verified_false[0] is None and verified_false[4] is True
    success = await manager._process_steamcmd_completion(1, 2, send, True, "", "", "", False, None)
    assert success[0] is True

    manager.execute_command = AsyncMock(return_value=(True, "", ""))
    manager._list_steamcmd_pids = AsyncMock(return_value=[])
    await manager._kill_steamcmd_processes(server, callback)
    manager._list_steamcmd_pids = AsyncMock(side_effect=[["11", "12"], ["12"]])
    await manager._kill_steamcmd_processes(server, callback)
    manager._list_steamcmd_pids = AsyncMock(side_effect=[["11"], []])
    await manager._kill_steamcmd_processes(server, callback)
    manager._list_steamcmd_pids = AsyncMock(side_effect=RuntimeError("pid"))
    await manager._kill_steamcmd_processes(server, callback)


@pytest.mark.asyncio
async def test_stray_process_cleanup_and_retry_terminal_paths(monkeypatch):
    server = _server()
    manager = _Manager()
    manager.execute_command = AsyncMock(side_effect=[(True, "12\n13", ""), (True, "", "")])
    await manager._kill_stray_cs2_processes(server, [])
    manager.execute_command = AsyncMock(side_effect=RuntimeError("ssh"))
    await manager._kill_stray_cs2_processes(server, AsyncMock())

    manager._prepare_steamcmd_retry = AsyncMock(return_value=True)
    manager._run_steamcmd_attempt = AsyncMock(
        return_value=(False, "", "disk full", "disk full", False)
    )
    monkeypatch.setattr(
        "services.ssh.game_steamcmd._legacy_cancel_requested", AsyncMock(return_value=False)
    )
    result = await manager._execute_steamcmd_with_retry(
        "cmd", server, progress_callback=[], max_retries=1
    )
    assert not result[0]

    manager.execute_command = AsyncMock(side_effect=[(True, "12", ""), (True, "12", "")])
    await manager._kill_stray_cs2_processes(server, [])
    manager.execute_command = AsyncMock(side_effect=[(True, "12", ""), (True, "", "")])
    await manager._kill_stray_cs2_processes(server, [])

    manager._run_steamcmd_attempt = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await manager._run_steamcmd_attempt("cmd", server, [], 10)

    manager._run_steamcmd_attempt = AsyncMock(return_value=(False, "", "fatal", "fatal", False))

    async def broken_completion():
        raise RuntimeError("verification")

    monkeypatch.setattr(
        "services.ssh.game_steamcmd._legacy_cancel_requested", AsyncMock(return_value=False)
    )
    result = await manager._execute_steamcmd_with_retry(
        "cmd",
        server,
        progress_callback=AsyncMock(),
        max_retries=0,
        completion_check=broken_completion,
    )
    assert not result[0]


class MockMonotonic:
    def __init__(self, values):
        self.values = iter(values)
        self.last = 0

    def __call__(self):
        try:
            self.last = next(self.values)
        except StopIteration:
            self.last += 100
        return self.last
