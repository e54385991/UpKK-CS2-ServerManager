"""隔离覆盖 SSH 连接池的失效、重连、清理和认证分支。"""

from __future__ import annotations

import asyncio
import importlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncssh
import pytest

from modules.models import AuthType
from services.ssh_connection_pool import PooledConnection, SSHConnectionPool


class _Conn:
    def __init__(self, *, closed=False):
        self.closed = closed
        self.close_calls = 0

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True
        self.close_calls += 1

    async def wait_closed(self):
        return None


def _server(**overrides):
    values = {
        "id": 1,
        "host": "ssh.example",
        "ssh_port": 22,
        "ssh_user": "cs2",
        "auth_type": AuthType.PASSWORD,
        "is_password_auth": True,
        "is_key_auth": False,
        "ssh_password": "secret",
        "ssh_key_path": "/tmp/key",
        "is_ssh_down": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _pool(**kwargs):
    previous = SSHConnectionPool._instance
    SSHConnectionPool._instance = None
    pool = SSHConnectionPool(**kwargs)
    SSHConnectionPool._instance = previous
    return pool


@pytest.mark.asyncio
async def test_pool_cleanup_loop_and_stale_generation_lifecycle(monkeypatch):
    pool = _pool(idle_timeout=2, max_lifetime=3)
    server = _server()
    key = pool._create_connection_key(server)
    dead = PooledConnection(_Conn(closed=True), key)
    idle = PooledConnection(_Conn(), key)
    idle.last_used = time.time() - 10
    old = PooledConnection(_Conn(), key)
    old.created_at = time.time() - 10
    pool.connections = {key: dead}
    await pool._cleanup_stale_connections()
    assert not pool.connections and dead.conn is None
    pool.connections = {key: idle}
    await pool._cleanup_stale_connections()
    assert idle.conn is None

    # Active leases are drained and closed only after the holder releases them.
    pool.connections = {key: old}
    pool._connection_index[id(old.conn)] = old
    old.acquire()
    await pool._cleanup_stale_connections()
    assert old.draining and old.conn is not None
    await pool.release_connection(server, old.conn)
    assert old.conn is None

    async def cancel_sleep(_seconds):
        raise asyncio.CancelledError

    pool_module = importlib.import_module("services.ssh_connection_pool")
    monkeypatch.setattr(pool_module.asyncio, "sleep", cancel_sleep)
    task = asyncio.create_task(pool._cleanup_loop())
    await task
    pool.cleanup_task = task
    await pool.stop_cleanup()
    await pool.close_all()


@pytest.mark.asyncio
async def test_pool_open_connection_auth_modes_and_get_failures(monkeypatch):
    pool = _pool()
    captured = []

    async def connect(**kwargs):
        captured.append(kwargs)
        return _Conn()

    pool_module = importlib.import_module("services.ssh_connection_pool")
    monkeypatch.setattr(pool_module.asyncssh, "connect", connect)
    await pool._open_connection(_server())
    await pool._open_connection(
        _server(is_password_auth=False, is_key_auth=True, auth_type=AuthType.KEY_FILE)
    )
    assert "password" in captured[0]
    assert captured[1]["client_keys"] == ["/tmp/key"]
    with pytest.raises(ValueError):
        await pool._open_connection(
            _server(is_password_auth=False, is_key_auth=False, auth_type="bad")
        )

    pool = _pool()
    server = _server()
    pool._open_connection = AsyncMock(return_value=_Conn())
    assert (await pool.get_connection(_server(is_ssh_down=True)))[0] is False
    reused = await pool.get_connection(server)
    assert reused[0] is True
    assert (await pool.get_connection(server))[2] == "Reused existing connection"
    await pool.close_all()

    for exc, phrase in (
        (asyncssh.PermissionDenied("denied"), "Authentication failed"),
        (asyncio.TimeoutError(), "timeout"),
        (asyncssh.Error(1, "bad"), "SSH error"),
        (ValueError("bad auth"), "bad auth"),
        (RuntimeError("socket"), "Connection error"),
    ):
        pool = _pool()
        pool._open_connection = AsyncMock(side_effect=exc)
        result = await pool.get_connection(server)
        assert result[0] is False and phrase in result[2]


@pytest.mark.asyncio
async def test_pool_reconnect_rate_limit_manual_and_release_paths(monkeypatch):
    server = _server()
    pool = _pool(max_lifetime=60, max_reconnections_per_hour=1)
    pool._open_connection = AsyncMock(side_effect=asyncssh.PermissionDenied("denied"))
    assert "Authentication failed" in (await pool.reconnect(server))[2]
    pool._open_connection = AsyncMock(side_effect=asyncssh.PermissionDenied("denied"))
    assert "认证失败" in (await pool.manual_reconnect(server))[2]
    pool._open_connection = AsyncMock(side_effect=asyncio.TimeoutError())
    assert "timeout" in (await pool.reconnect(server))[2]
    assert "超时" in (await pool.manual_reconnect(server))[2]
    pool._open_connection = AsyncMock(side_effect=asyncssh.Error(1, "bad"))
    assert "SSH error" in (await pool.reconnect(server))[2]
    assert "SSH error" in (await pool.manual_reconnect(server))[2]
    pool._open_connection = AsyncMock(side_effect=ValueError("bad"))
    assert (await pool.reconnect(server))[2] == "bad"
    assert (await pool.manual_reconnect(server))[2] == "bad"
    pool._open_connection = AsyncMock(side_effect=RuntimeError("socket"))
    assert "Connection error" in (await pool.reconnect(server))[2]
    assert "连接错误" in (await pool.manual_reconnect(server))[2]

    pool = _pool(max_reconnections_per_hour=1)
    key = pool._create_connection_key(server)
    active = PooledConnection(_Conn(), key)
    active.reconnection_attempts = [time.time()]
    pool.connections[key] = active
    assert (await pool.reconnect(server))[0] is False
    assert (await pool.reset_reconnection_counter(server))[0] is True
    assert await pool.reset_reconnection_counter(_server(host="missing.example")) == (
        True,
        "无活动连接，无需重置 | No active connection, nothing to reset",
    )

    # Unknown generations are harmless; an active lease can be explicitly released.
    await pool.release_connection(server, _Conn())
    await pool.remove_connection(_server(host="missing.example"))
    await pool.close_all()


@pytest.mark.asyncio
async def test_pool_reconnect_reuses_replacement_and_reports_stats(monkeypatch):
    server = _server()
    pool = _pool()
    key = pool._create_connection_key(server)
    current = PooledConnection(_Conn(), key)
    current.acquire()
    pool.connections[key] = current
    pool._connection_index[id(current.conn)] = current
    failed = _Conn()
    replacement = PooledConnection(_Conn(), key)
    pool.connections[key] = replacement
    pool._connection_index[id(replacement.conn)] = replacement
    replacement.created_at = time.time()
    result = await pool.reconnect_for_connection(server, failed)
    assert result[0] is True and result[1] is replacement.conn
    info = await pool.get_connection_info(server)
    assert info["connected"] is True and info["active_leases"] == 1
    assert (await pool.get_connection_info(_server(host="none.example")))["connected"] is False
    stats = await pool.get_pool_stats()
    assert stats["total_connections"] == 1
    await pool.close_all()
