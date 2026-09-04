"""隔离动作控制台 WebSocket 的认证、输入中继和清理路径。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes.actions import console


class _WebSocket:
    def __init__(self, messages=()):
        self.messages = list(messages)
        self.sent = []
        self.accepted = False
        self.closed = False

    async def accept(self):
        self.accepted = True

    async def close(self):
        self.closed = True

    async def send_json(self, value):
        self.sent.append(value)

    async def receive_text(self):
        await asyncio.sleep(0)
        if not self.messages:
            raise console.WebSocketDisconnect()
        value = self.messages.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class _Output:
    def __init__(self, chunks=(b"hello", b"")):
        self.chunks = list(chunks)

    async def read(self, _size):
        await asyncio.sleep(0)
        return self.chunks.pop(0) if self.chunks else b""


class _Process:
    def __init__(self):
        self.stdout = _Output()
        self.stdin = SimpleNamespace(write=lambda value: setattr(self, "input", value), drain=AsyncMock())
        self.sizes = []
        self.terminated = False
        self.killed = False

    def change_terminal_size(self, cols, rows):
        self.sizes.append((cols, rows))

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    async def wait_closed(self):
        return None


def _server():
    return SimpleNamespace(id=4, host="server.test", session_manager="tmux")


@pytest.mark.asyncio
async def test_console_helpers_cover_session_probe_relay_and_cleanup(monkeypatch):
    ssh = SimpleNamespace(execute_command=AsyncMock())
    server = _server()
    monkeypatch.setattr(console, "find_running_session_manager", AsyncMock(return_value="screen"))
    assert await console._active_game_session(ssh, 4, server) == "screen"
    monkeypatch.setattr(console, "find_running_session_manager", AsyncMock(side_effect=RuntimeError("probe")))
    assert await console._active_game_session(ssh, 4, server) is None

    process = _Process()
    ws = _WebSocket(
        [
            json.dumps({"type": "input", "data": "status\n"}),
            json.dumps({"type": "resize", "cols": 120, "rows": 40}),
            json.dumps({"type": "ping"}),
            json.dumps({"type": "unknown"}),
            json.dumps({"type": "disconnect"}),
        ]
    )
    await console._relay_console(ws, process, allow_ping=True)
    assert process.input == b"status\n"
    assert process.sizes == [(120, 40)]
    assert any(item["type"] == "pong" for item in ws.sent)
    assert any(item["type"] == "output" for item in ws.sent)

    await console._close_console_process(None)
    await console._close_console_process(process)
    assert process.terminated
    failing = _Process()
    failing.terminate = lambda: (_ for _ in ()).throw(RuntimeError("term"))
    await console._close_console_process(failing)
    assert failing.killed


@pytest.mark.asyncio
async def test_ssh_console_endpoint_auth_connection_and_interactive_errors(monkeypatch):
    ws = _WebSocket()
    monkeypatch.setattr(console, "authenticate_websocket", AsyncMock(return_value=(None, None)))
    await console.ssh_console_websocket(ws, 4)
    assert not ws.accepted

    ssh = SimpleNamespace(connect=AsyncMock(return_value=(False, "refused")), disconnect=AsyncMock())
    monkeypatch.setattr(console, "authenticate_websocket", AsyncMock(return_value=(object(), _server())))
    monkeypatch.setattr(console, "SSHManager", lambda: ssh)
    ws = _WebSocket()
    await console.ssh_console_websocket(ws, 4)
    assert ws.accepted and ws.sent[-1]["type"] == "error" and ws.closed

    process = _Process()
    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(True, "ok")),
        disconnect=AsyncMock(),
        create_interactive_process=AsyncMock(return_value=process),
    )
    monkeypatch.setattr(console, "SSHManager", lambda: ssh)
    ws = _WebSocket(
        [
            json.dumps({"type": "input", "data": "echo\n"}),
            json.dumps({"type": "resize", "cols": 90, "rows": 30}),
            json.dumps({"type": "disconnect"}),
        ]
    )
    await console.ssh_console_websocket(ws, 4)
    assert process.terminated and process.sizes == [(90, 30)]
    ssh.disconnect.assert_awaited_once()

    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(True, "ok")),
        disconnect=AsyncMock(),
        create_interactive_process=AsyncMock(side_effect=RuntimeError("pty unavailable")),
    )
    monkeypatch.setattr(console, "SSHManager", lambda: ssh)
    ws = _WebSocket()
    await console.ssh_console_websocket(ws, 4)
    assert "Console error" in ws.sent[-1]["message"]


@pytest.mark.asyncio
async def test_game_console_endpoint_covers_not_running_and_attached_relay(monkeypatch):
    server = _server()
    monkeypatch.setattr(console, "authenticate_websocket", AsyncMock(return_value=(object(), server)))
    ssh = SimpleNamespace(connect=AsyncMock(return_value=(False, "offline")), disconnect=AsyncMock())
    monkeypatch.setattr(console, "SSHManager", lambda: ssh)
    ws = _WebSocket()
    await console.game_console_websocket(ws, 4)
    assert ws.sent[-1]["type"] == "error" and ws.closed

    ssh = SimpleNamespace(connect=AsyncMock(return_value=(True, "ok")), disconnect=AsyncMock())
    monkeypatch.setattr(console, "SSHManager", lambda: ssh)
    monkeypatch.setattr(console, "_active_game_session", AsyncMock(return_value=None))
    ws = _WebSocket()
    await console.game_console_websocket(ws, 4)
    assert "not running" in ws.sent[-1]["message"]
    ssh.disconnect.assert_awaited_once()

    process = _Process()
    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(True, "ok")),
        disconnect=AsyncMock(),
        create_interactive_process=AsyncMock(return_value=process),
    )
    monkeypatch.setattr(console, "SSHManager", lambda: ssh)
    monkeypatch.setattr(console, "_active_game_session", AsyncMock(return_value="tmux"))
    ws = _WebSocket([json.dumps({"type": "disconnect"})])
    await console.game_console_websocket(ws, 4)
    assert process.terminated and ssh.disconnect.await_count == 1
    assert "tmux" in ssh.create_interactive_process.await_args.args[0]
