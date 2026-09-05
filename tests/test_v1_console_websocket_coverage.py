"""隔离覆盖 v1 控制台 WebSocket 的认证、生命周期和中继逻辑。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes.v1 import console


class _WebSocket:
    def __init__(self, messages=(), *, headers=None, cookies=None):
        self.headers = headers or {"host": "panel.test"}
        self.cookies = cookies or {}
        self.messages = list(messages)
        self.sent = []
        self.closed = []
        self.accepted = False

    async def close(self, **kwargs):
        self.closed.append(kwargs)

    async def accept(self):
        self.accepted = True

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


class _Stdout:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def read(self, _size):
        await asyncio.sleep(0)
        return self.chunks.pop(0) if self.chunks else b""


class _Process:
    def __init__(self):
        self.stdout = _Stdout([b"hello\n", b""])
        self.stdin = SimpleNamespace(
            write=lambda value: setattr(self, "input", value), drain=AsyncMock()
        )
        self.sizes = []
        self.terminated = False

    def change_terminal_size(self, cols, rows):
        self.sizes.append((cols, rows))

    def terminate(self):
        self.terminated = True

    async def wait_closed(self):
        return None


def _server(**overrides):
    values = {"id": 2, "host": "server.test", "session_manager": "tmux"}
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_console_authentication_rejects_origin_token_and_access(monkeypatch):
    denied = _WebSocket(headers={"host": "panel.test", "origin": "https://evil.test"})
    assert await console._authenticate_console(denied, 2) is None
    assert denied.closed[0]["code"] == 4403

    no_token = _WebSocket()
    assert await console._authenticate_console(no_token, 2) is None
    assert no_token.closed[0]["code"] == 4401

    class _Context:
        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(console, "async_session_maker", lambda: _Context())
    monkeypatch.setattr(console, "web_session_cookie_name", lambda: "session")
    monkeypatch.setattr(console, "_get_active_user_for_token", AsyncMock(return_value=None))
    invalid = _WebSocket(cookies={"session": "bad"})
    assert await console._authenticate_console(invalid, 2) is None
    assert invalid.closed[0]["code"] == 4401

    user = SimpleNamespace(id=1)
    monkeypatch.setattr(console, "_get_active_user_for_token", AsyncMock(return_value=user))
    monkeypatch.setattr(
        console,
        "require_server_access",
        AsyncMock(side_effect=HTTPException(status_code=404, detail="missing")),
    )
    missing = _WebSocket(cookies={"session": "valid"})
    assert await console._authenticate_console(missing, 2) is None
    assert missing.closed[0]["code"] == 4404

    monkeypatch.setattr(console, "require_server_access", AsyncMock(return_value=_server()))
    allowed = _WebSocket(
        cookies={"session": "valid"},
        headers={"host": "panel.test", "origin": "https://panel.test"},
    )
    assert (await console._authenticate_console(allowed, 2)).id == 2


@pytest.mark.asyncio
async def test_console_session_start_covers_ssh_game_preflight_and_success(monkeypatch):
    server = _server(id=3)
    ws = _WebSocket()
    ssh = SimpleNamespace(connect=AsyncMock(return_value=(False, "refused")), conn=None)
    assert await console._start_console_session(ws, ssh, server, "ssh") is None
    assert ws.sent[-1]["type"] == "error"

    ws = _WebSocket()
    ssh = SimpleNamespace(connect=AsyncMock(return_value=(True, "ok")), conn=None)
    assert await console._start_console_session(ws, ssh, server, "ssh") is None
    assert "no session" in ws.sent[-1]["message"]

    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(True, "ok")),
        conn=object(),
        execute_command=AsyncMock(),
        create_interactive_process=AsyncMock(),
    )
    monkeypatch.setattr(
        console, "find_running_session_manager", AsyncMock(side_effect=RuntimeError("probe"))
    )
    ws = _WebSocket()
    assert await console._start_console_session(ws, ssh, server, "game") is None
    assert "check server status" in ws.sent[-1]["message"]

    monkeypatch.setattr(console, "find_running_session_manager", AsyncMock(return_value=None))
    ws = _WebSocket()
    assert await console._start_console_session(ws, ssh, server, "game") is None
    assert "not running" in ws.sent[-1]["message"]

    process = _Process()
    ssh.create_interactive_process.return_value = process
    monkeypatch.setattr(console, "find_running_session_manager", AsyncMock(return_value="screen"))
    ws = _WebSocket()
    assert await console._start_console_session(ws, ssh, server, "game") is process
    assert "CS2 server console" in ws.sent[-1]["message"]

    ws = _WebSocket()
    assert await console._start_console_session(ws, ssh, server, "ssh") is process
    assert "Connected to" in ws.sent[-1]["message"]


@pytest.mark.asyncio
async def test_console_input_relay_handles_invalid_input_resize_ping_and_disconnect():
    process = _Process()
    websocket = _WebSocket(
        [
            "not-json",
            json.dumps({"type": "input", "data": "status\n"}),
            json.dumps({"type": "resize", "cols": 120, "rows": 40}),
            json.dumps({"type": "ping"}),
            json.dumps({"type": "unknown"}),
            json.dumps({"type": "disconnect"}),
        ]
    )
    await console._relay_console_input(websocket, process)
    assert process.input == b"status\n"
    assert process.stdin.drain.await_count == 1
    assert process.sizes == [(120, 40)]
    assert {item["type"] for item in websocket.sent} >= {"output", "pong"}


@pytest.mark.asyncio
async def test_console_run_cleanup_handles_websocket_disconnect_and_errors(monkeypatch):
    process = _Process()
    ssh = SimpleNamespace(disconnect=AsyncMock())
    monkeypatch.setattr(console, "SSHManager", lambda: ssh)
    monkeypatch.setattr(console, "_start_console_session", AsyncMock(return_value=process))
    monkeypatch.setattr(
        console, "_relay_console_input", AsyncMock(side_effect=console.WebSocketDisconnect())
    )
    websocket = _WebSocket()
    await console._run_console(websocket, _server(), kind="ssh")
    assert process.terminated
    ssh.disconnect.assert_awaited_once()

    ssh = SimpleNamespace(disconnect=AsyncMock())
    monkeypatch.setattr(console, "SSHManager", lambda: ssh)
    monkeypatch.setattr(console, "_start_console_session", AsyncMock(return_value=None))
    await console._run_console(_WebSocket(), _server(), kind="game")
    ssh.disconnect.assert_awaited_once()

    ssh = SimpleNamespace(disconnect=AsyncMock())
    monkeypatch.setattr(console, "SSHManager", lambda: ssh)
    monkeypatch.setattr(
        console, "_start_console_session", AsyncMock(side_effect=RuntimeError("boom"))
    )
    websocket = _WebSocket()
    await console._run_console(websocket, _server(), kind="ssh")
    assert websocket.sent[-1]["type"] == "error"
    ssh.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_console_websocket_endpoints_auth_accept_and_dispatch(monkeypatch):
    server = _server()
    for endpoint, kind in (
        (console.ssh_console_websocket, "ssh"),
        (console.game_console_websocket, "game"),
    ):
        websocket = _WebSocket()
        monkeypatch.setattr(console, "_authenticate_console", AsyncMock(return_value=server))
        runner = AsyncMock()
        monkeypatch.setattr(console, "_run_console", runner)
        await endpoint(websocket, 2)
        assert websocket.accepted
        runner.assert_awaited_once_with(websocket, server, kind=kind)
