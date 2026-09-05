"""覆盖设置路由的 WebSocket 生命周期、Redis 访问控制和 SSH 异常映射。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncssh
import pytest
from fastapi import HTTPException, WebSocketDisconnect

from api.routes import setup


class _Ws:
    def __init__(self, receives=()):
        self.receives = list(receives)
        self.accepted = False
        self.sent = []
        self.closed = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, value):
        self.sent.append(value)

    async def receive_text(self):
        value = self.receives.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def close(self, **kwargs):
        self.closed.append(kwargs)


class _Conn:
    def __init__(self):
        self.closed = False
        self.waited = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.waited = True


class _Db:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def _request(**overrides):
    values = dict(
        name="demo",
        host="example.test",
        ssh_user="root",
        ssh_password="secret",
        cs2_username="cs2server",
        cs2_password="fixed-pass",
        save_config=False,
        open_game_ports=False,
    )
    values.update(overrides)
    return setup.ServerSetupRequest(**values)


@pytest.mark.asyncio
async def test_setup_websocket_manager_conflicts_and_send_failures(monkeypatch):
    manager = setup.SetupWebSocket()
    first = _Ws()
    assert await manager.connect(first, "session", 1) is True
    same_user = _Ws()
    assert await manager.connect(same_user, "session", 1) is True
    assert manager.active_connections["session"] == (1, same_user)
    other = _Ws()
    assert await manager.connect(other, "session", 2) is False
    assert other.closed == [{"code": 4409, "reason": "Setup session is already in use"}]
    await manager.send_message("session", 1, {"type": "log"})
    assert same_user.sent == [{"type": "log"}]
    await manager.send_message("session", 2, {"type": "ignored"})
    same_user.send_json = AsyncMock(side_effect=RuntimeError("closed"))
    await manager.send_message("session", 1, {"type": "log"})
    assert "session" not in manager.active_connections
    manager.disconnect("missing")
    manager.disconnect("session", first)

    monkeypatch.setattr(setup.setup_ws, "send_message", AsyncMock(side_effect=RuntimeError("gone")))
    await setup.send_setup_progress("session", 1, "hello")
    await setup.send_setup_progress(None, 1, "ignored")


@pytest.mark.asyncio
async def test_setup_websocket_endpoint_auth_disconnect_and_cleanup(monkeypatch):
    monkeypatch.setattr(setup, "authenticate_websocket", AsyncMock(return_value=(None, None)))
    unauthenticated = _Ws()
    await setup.setup_progress_websocket(unauthenticated, "unauth")
    assert not unauthenticated.accepted

    user = SimpleNamespace(id=9)
    monkeypatch.setattr(setup, "authenticate_websocket", AsyncMock(return_value=(user, None)))
    setup.setup_ws.active_connections["busy"] = (3, _Ws())
    busy = _Ws()
    await setup.setup_progress_websocket(busy, "busy")
    assert busy.closed
    setup.setup_ws.disconnect("busy")

    connected = _Ws(["hello", WebSocketDisconnect()])
    await setup.setup_progress_websocket(connected, "ok")
    assert connected.accepted and connected.sent[0]["type"] == "info"
    assert "ok" not in setup.setup_ws.active_connections

    failing = _Ws([RuntimeError("receive failed")])
    await setup.setup_progress_websocket(failing, "error")
    assert "error" not in setup.setup_ws.active_connections


@pytest.mark.asyncio
async def test_auto_setup_success_and_all_ssh_error_mappings(monkeypatch):
    monkeypatch.setattr(setup, "require_captcha", AsyncMock())
    connection = _Conn()
    monkeypatch.setattr(setup.asyncssh, "connect", AsyncMock(return_value=connection))
    monkeypatch.setattr(setup, "_detect_setup_host", AsyncMock())
    monkeypatch.setattr(setup, "_install_setup_dependencies", AsyncMock())
    monkeypatch.setattr(setup, "_install_legacy_libssl", AsyncMock())
    monkeypatch.setattr(setup, "_configure_setup_user", AsyncMock())
    monkeypatch.setattr(setup, "_persist_setup_configuration", AsyncMock(return_value="redis-key"))
    monkeypatch.setattr(setup, "send_setup_progress", AsyncMock())
    result = await setup.auto_setup_server(_request(session_id="sid"), SimpleNamespace(id=7), _Db())
    assert result.success and result.initialized_server_id == "redis-key"
    assert connection.closed and connection.waited

    for exception, status_code in (
        (asyncssh.PermissionDenied("denied"), 400),
        (asyncio.TimeoutError(), 504),
        (asyncssh.Error(1, "ssh"), 500),
        (RuntimeError("unexpected"), 500),
    ):
        conn = _Conn()
        monkeypatch.setattr(setup.asyncssh, "connect", AsyncMock(side_effect=exception))
        with pytest.raises(HTTPException) as exc_info:
            await setup.auto_setup_server(_request(), SimpleNamespace(id=7), _Db())
        assert exc_info.value.status_code == status_code
        assert conn.closed is False

    monkeypatch.setattr(setup.asyncssh, "connect", AsyncMock(return_value=_Conn()))
    monkeypatch.setattr(
        setup, "_detect_setup_host", AsyncMock(side_effect=HTTPException(422, "bad"))
    )
    with pytest.raises(HTTPException) as exc_info:
        await setup.auto_setup_server(_request(), SimpleNamespace(id=7), _Db())
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_initialized_server_redis_listing_ownership_and_delete_paths(monkeypatch):
    user = SimpleNamespace(id=7)
    records = [
        {
            "key": "init:7:one",
            "user_id": 7,
            "name": "demo",
            "host": "example.test",
            "ssh_port": 22,
            "ssh_user": "cs2",
            "ssh_password": "secret",
            "game_directory": "/srv/cs2",
            "created_at": 1.0,
            "ignored": "not returned",
        }
    ]
    redis = SimpleNamespace(
        get_initialized_servers=AsyncMock(return_value=records),
        get_initialized_server=AsyncMock(return_value=records[0]),
        delete_initialized_server=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(setup, "redis_manager", redis)
    listed = await setup.list_initialized_servers(user)
    assert listed[0].ssh_user == "cs2" and not hasattr(listed[0], "ssh_password")
    detail = await setup.get_initialized_server("init:7:one", user)
    assert detail.ssh_password == "secret"
    assert await setup.delete_initialized_server("init:7:one", user) == {
        "success": True,
        "message": "Initialized server deleted successfully",
    }

    for endpoint in (setup.get_initialized_server, setup.delete_initialized_server):
        redis.get_initialized_server = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc_info:
            await endpoint("missing", user)
        assert exc_info.value.status_code == 404

    redis.get_initialized_server = AsyncMock(return_value={**records[0], "user_id": 8})
    with pytest.raises(HTTPException) as exc_info:
        await setup.get_initialized_server("other", user)
    assert exc_info.value.status_code == 403
    with pytest.raises(HTTPException) as exc_info:
        await setup.delete_initialized_server("other", user)
    assert exc_info.value.status_code == 403

    redis.get_initialized_server = AsyncMock(return_value=records[0])
    redis.delete_initialized_server = AsyncMock(return_value=False)
    with pytest.raises(HTTPException) as exc_info:
        await setup.delete_initialized_server("init:7:one", user)
    assert exc_info.value.status_code == 500
