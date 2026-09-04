"""Cover CS2 startup branches using a fake SSH transport."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.ssh import game_start as game_start_module
from services.ssh.game_start import GameStartMixin
from services.ssh_manager import SSHManager


def _server(**overrides):
    values = dict(
        id=8,
        game_directory="/srv/cs2",
        session_manager="tmux",
        default_map="de_dust2",
        game_mode="competitive",
        game_type=None,
        additional_parameters="",
        max_players=16,
        server_name="Test",
        game_port=27015,
        client_port=None,
        ip_address=None,
        steam_account_token=None,
        server_password=None,
        rcon_password=None,
        tv_enable=False,
        tv_port=None,
        backend_url=None,
        api_key=None,
        cpu_affinity=None,
        discord_crash_restart_min_interval_minutes=10,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _manager(server, *, sessions=("tmux",)):
    manager = SSHManager(use_pool=False)
    manager.connect = AsyncMock(return_value=(True, "connected"))
    manager.disconnect = AsyncMock()
    manager._cs2_executable_exists_connected = AsyncMock(return_value=(True, "./cs2"))
    manager._configured_session_manager_available_connected = AsyncMock(
        return_value=(True, "tmux is available")
    )
    manager._stop_server_sessions_connected = AsyncMock(return_value=(True, []))
    manager._kill_stray_cs2_processes = AsyncMock()
    manager.perform_server_selfcheck = AsyncMock(return_value=(True, "ok"))
    manager._running_server_session_managers = AsyncMock(return_value=list(sessions))
    manager.execute_command = AsyncMock(return_value=(True, "exists", ""))
    manager.execute_command_streaming = AsyncMock(return_value=(True, "", ""))
    return manager


async def _no_sleep(*_args, **_kwargs):
    return None


@pytest.mark.asyncio
async def test_start_server_short_circuits_connection_binary_and_manager_failures():
    server = _server()
    manager = SSHManager(use_pool=False)
    manager.connect = AsyncMock(return_value=(False, "offline"))
    assert await manager.start_server(server) == (False, "Connection failed: offline")

    manager = _manager(server)
    manager._cs2_executable_exists_connected.return_value = (False, "missing")
    ok, message = await manager.start_server(server)
    assert not ok and "missing" in message

    manager = _manager(server)
    manager._configured_session_manager_available_connected.return_value = (False, "screen missing")
    ok, message = await manager.start_server(server)
    assert not ok and "install it" in message

    manager = _manager(server)
    manager._stop_server_sessions_connected.return_value = (False, ["tmux"])
    ok, message = await manager.start_server(server)
    assert not ok and "could not be terminated" in message


@pytest.mark.asyncio
async def test_start_server_handles_invalid_config_script_failure_and_start_failure(monkeypatch):
    server = _server(api_key=None)
    manager = _manager(server)
    original_normalize = game_start_module.normalize_default_map
    monkeypatch.setattr(game_start_module, "normalize_default_map", lambda _value: (_ for _ in ()).throw(ValueError("bad map")))
    monkeypatch.setattr("services.ssh.game_start.asyncio.sleep", _no_sleep)
    ok, message = await manager.start_server(server)
    assert not ok and "Invalid startup configuration" in message
    monkeypatch.setattr(game_start_module, "normalize_default_map", original_normalize)

    server = _server()
    manager = _manager(server)
    manager.execute_command = AsyncMock(side_effect=lambda command, **_kwargs: (
        (False, "", "cannot write") if command.startswith("test -f") else
        (False, "", "cannot deploy") if command.startswith("cat >") else
        (True, "exists" if command.startswith("test -f") else "", "")
    ))
    monkeypatch.setattr("services.ssh.game_start.asyncio.sleep", _no_sleep)
    ok, message = await manager.start_server(server)
    assert ok, message

    server = _server(api_key="secret")
    manager = _manager(server)
    manager.execute_command = AsyncMock(side_effect=[
        (True, "", ""),
        (True, "exists", ""),
        (False, "", "start failed"),
    ])
    monkeypatch.setattr("services.ssh.game_start.asyncio.sleep", _no_sleep)
    ok, message = await manager.start_server(server)
    assert not ok and "Start command failed" in message


def _patch_monitor(monkeypatch, *, can_restart=(False, "restart disabled")):
    monitor = SimpleNamespace(
        can_restart=lambda _id: can_restart,
        record_restart=lambda _id: None,
        queue_restart_notification=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr("services.ssh.game_start.server_monitor", monitor)
    return monitor


@pytest.mark.asyncio
async def test_start_server_reports_immediate_crash_and_auto_restart(monkeypatch):
    monkeypatch.setattr("services.ssh.game_start.asyncio.sleep", _no_sleep)
    server = _server()
    manager = _manager(server, sessions=())
    manager._running_server_session_managers = AsyncMock(return_value=[])
    manager.execute_command = AsyncMock(return_value=(True, "map failed; error; quit", ""))
    _patch_monitor(monkeypatch)
    ok, message = await manager.start_server(server, AsyncMock())
    assert not ok and "process exited" in message

    server = _server()
    manager = _manager(server, sessions=())
    manager._running_server_session_managers = AsyncMock(side_effect=[[], []])
    manager.execute_command = AsyncMock(side_effect=lambda command, **_kwargs: (
        (True, "console error", "") if "console.log" in command else
        (True, "No core dump", "")
    ))
    _patch_monitor(monkeypatch, can_restart=(True, "available"))
    recursive = AsyncMock(return_value=(True, "restarted"))
    manager.start_server = recursive
    # Call the class implementation once; its recursive auto-restart uses the fake method above.
    ok, message = await GameStartMixin.start_server(manager, server, AsyncMock())
    assert (ok, message) == (True, "restarted")
    recursive.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_server_verifies_quick_crash_process_port_and_diagnostics(monkeypatch):
    monkeypatch.setattr("services.ssh.game_start.asyncio.sleep", _no_sleep)
    for verification, expected in (("process", "process verified"), ("port", "port listening")):
        server = _server()
        manager = _manager(server, sessions=())
        manager._running_server_session_managers = AsyncMock(side_effect=[["tmux"], ["tmux"], []])

        async def execute(command, **_kwargs):
            if "pgrep -f" in command:
                return True, "running" if verification == "process" else "stopped", ""
            if "netstat -tuln" in command:
                return True, "tcp 0 0 :27015", ""
            return True, "exists", ""

        manager.execute_command = execute
        ok, message = await manager.start_server(server)
        assert ok and expected in message

    server = _server()
    manager = _manager(server, sessions=())
    manager._running_server_session_managers = AsyncMock(side_effect=[["tmux"], ["tmux"], []])
    _patch_monitor(monkeypatch)

    async def diagnostic_execute(command, **_kwargs):
        if "pgrep -f" in command:
            return True, "stopped", ""
        if "netstat -tuln" in command:
            return True, "not listening", ""
        if "console.log" in command:
            return True, "bind: address already in use permission denied map failed library.so segmentation fault failed to load error", ""
        if "core*" in command:
            return True, "/tmp/core.1", ""
        if "ldd" in command:
            return True, "libfoo.so => not found", ""
        if "steamclient.so" in command:
            return True, "MISSING steamclient.so", ""
        if "test -f" in command:
            return True, "missing", ""
        return True, "", ""

    manager.execute_command = diagnostic_execute
    ok, message = await manager.start_server(server)
    assert not ok and "Startup Diagnostics" in message and "Missing Libraries" in message


@pytest.mark.asyncio
async def test_start_server_returns_wrapped_exception_and_refresh_failure(monkeypatch):
    monkeypatch.setattr("services.ssh.game_start.asyncio.sleep", _no_sleep)
    server = _server()
    manager = _manager(server)
    manager._running_server_session_managers = AsyncMock(side_effect=RuntimeError("session error"))
    ok, message = await manager.start_server(server)
    assert not ok and message == "Start error: session error"
