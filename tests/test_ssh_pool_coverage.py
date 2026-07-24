"""Edge-case coverage for bounded, generation-aware SSH pooling."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncssh
import pytest

from modules.models import AuthType
from services.ssh_connection_pool import (
    ConnectionLease,
    PooledConnection,
    SSHConnectionPool,
)


class _Connection:
    def __init__(self, *, closed: bool = False) -> None:
        self.closed = closed
        self.close_calls = 0
        self.wait_closed_calls = 0

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True

    async def wait_closed(self) -> None:
        self.wait_closed_calls += 1


def _server(**overrides):
    values = {
        "id": 10,
        "host": "game.example",
        "ssh_port": 22,
        "ssh_user": "cs2",
        "auth_type": AuthType.PASSWORD,
        "credential_revision": 3,
        "is_ssh_down": False,
        "is_password_auth": True,
        "is_key_auth": False,
        "ssh_password": "secret",
        "ssh_key_path": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def pool(monkeypatch):
    monkeypatch.setattr(SSHConnectionPool, "_instance", None)
    return SSHConnectionPool(max_connections=2, acquire_timeout=0.01)


@pytest.mark.asyncio
async def test_connection_lease_is_idempotent_and_an_async_context(pool, monkeypatch):
    connection = _Connection()
    release = AsyncMock()
    monkeypatch.setattr(pool, "release_lease", release)
    key = pool._create_connection_key(_server())
    lease = ConnectionLease(pool, key, 1, connection)

    async with lease as checked_out:
        assert checked_out is connection

    await lease.release()
    release.assert_awaited_once_with(lease)
    assert lease.released is True


@pytest.mark.asyncio
async def test_cleanup_evicts_unused_key_locks(pool):
    key = pool._create_connection_key(_server())
    pool._key_locks[key] = asyncio.Lock()

    await pool._cleanup_stale_connections()

    assert key not in pool._key_locks


@pytest.mark.asyncio
async def test_capacity_reservation_times_out(pool):
    pool._capacity = SimpleNamespace(acquire=AsyncMock(side_effect=asyncio.TimeoutError))

    assert await pool._reserve_capacity() is False


@pytest.mark.asyncio
async def test_stale_connection_is_closed_before_capacity_failure(pool, monkeypatch):
    server = _server()
    key = pool._create_connection_key(server)
    stale = PooledConnection(_Connection(closed=True), key, 1)
    pool.connections[key] = stale
    close = AsyncMock()
    monkeypatch.setattr(pool, "_close_connection", close)
    monkeypatch.setattr(pool, "_reserve_capacity", AsyncMock(return_value=False))

    success, connection, message = await pool.get_connection(server)

    assert success is False
    assert connection is None
    assert "at capacity" in message
    close.assert_awaited_once_with(stale)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "message"),
    [
        (asyncssh.PermissionDenied("denied"), "Authentication failed"),
        (asyncio.TimeoutError(), "connection timeout"),
        (asyncssh.ConnectionLost("reset"), "SSH error: reset"),
        (ValueError("unsupported"), "unsupported"),
        (RuntimeError("socket failed"), "Connection error: socket failed"),
    ],
)
async def test_get_connection_maps_open_failures(pool, monkeypatch, error, message):
    abort = Mock()
    monkeypatch.setattr(pool, "_reserve_capacity", AsyncMock(return_value=True))
    monkeypatch.setattr(pool, "_open_connection", AsyncMock(side_effect=error))
    monkeypatch.setattr(pool, "_abort_open", abort)

    success, connection, detail = await pool.get_connection(_server())

    assert success is False
    assert connection is None
    assert message in detail
    abort.assert_called_once_with(None)


@pytest.mark.asyncio
async def test_get_connection_preserves_cancellation(pool, monkeypatch):
    abort = Mock()
    monkeypatch.setattr(pool, "_reserve_capacity", AsyncMock(return_value=True))
    monkeypatch.setattr(pool, "_open_connection", AsyncMock(side_effect=asyncio.CancelledError))
    monkeypatch.setattr(pool, "_abort_open", abort)

    with pytest.raises(asyncio.CancelledError):
        await pool.get_connection(_server())
    abort.assert_called_once_with(None)


@pytest.mark.asyncio
async def test_acquire_lease_rejects_failed_or_replaced_connections(pool, monkeypatch):
    server = _server()
    connection = _Connection()
    get_connection = AsyncMock(return_value=(False, None, "offline"))
    monkeypatch.setattr(pool, "get_connection", get_connection)
    assert await pool.acquire_lease(server) == (False, None, "offline")

    get_connection.return_value = (True, connection, "connected")
    result = await pool.acquire_lease(server)
    assert result == (False, None, "SSH connection changed while acquiring lease")


@pytest.mark.asyncio
async def test_reconnect_returns_capacity_failure(pool, monkeypatch):
    monkeypatch.setattr(pool, "_reserve_capacity", AsyncMock(return_value=False))

    success, connection, message = await pool.reconnect(_server())

    assert success is False
    assert connection is None
    assert "at capacity" in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "message"),
    [
        (asyncssh.PermissionDenied("denied"), "Authentication failed"),
        (asyncio.TimeoutError(), "connection timeout"),
        (asyncssh.ConnectionLost("reset"), "SSH error: reset"),
        (ValueError("unsupported"), "unsupported"),
        (RuntimeError("socket failed"), "Connection error: socket failed"),
    ],
)
async def test_reconnect_maps_open_failures(pool, monkeypatch, error, message):
    abort = Mock()
    monkeypatch.setattr(pool, "_reserve_capacity", AsyncMock(return_value=True))
    monkeypatch.setattr(pool, "_open_connection", AsyncMock(side_effect=error))
    monkeypatch.setattr(pool, "_abort_open", abort)

    success, connection, detail = await pool.reconnect(_server())

    assert success is False
    assert connection is None
    assert message in detail
    abort.assert_called_once_with(None)


@pytest.mark.asyncio
async def test_reconnect_preserves_cancellation(pool, monkeypatch):
    abort = Mock()
    monkeypatch.setattr(pool, "_reserve_capacity", AsyncMock(return_value=True))
    monkeypatch.setattr(pool, "_open_connection", AsyncMock(side_effect=asyncio.CancelledError))
    monkeypatch.setattr(pool, "_abort_open", abort)

    with pytest.raises(asyncio.CancelledError):
        await pool.reconnect(_server())
    abort.assert_called_once_with(None)


@pytest.mark.asyncio
async def test_reconnect_lease_marks_previous_and_binds_current_generation(pool, monkeypatch):
    server = _server()
    key = pool._create_connection_key(server)
    previous = ConnectionLease(pool, key, 1, _Connection())
    connection = _Connection()
    pooled = PooledConnection(connection, key, 8)
    pool.connections[key] = pooled
    monkeypatch.setattr(
        pool,
        "reconnect",
        AsyncMock(return_value=(True, connection, "reconnected")),
    )

    success, lease, message = await pool.reconnect_lease(server, previous)

    assert success is True
    assert message == "reconnected"
    assert previous.released is True
    assert lease is not None
    assert lease.generation == 8
    assert lease.connection is connection


@pytest.mark.asyncio
async def test_reconnect_lease_rejects_failure_and_race(pool, monkeypatch):
    server = _server()
    connection = _Connection()
    reconnect = AsyncMock(return_value=(False, None, "failed"))
    monkeypatch.setattr(pool, "reconnect", reconnect)
    assert await pool.reconnect_lease(server) == (False, None, "failed")

    reconnect.return_value = (True, connection, "connected")
    assert await pool.reconnect_lease(server) == (
        False,
        None,
        "SSH connection changed while reconnecting",
    )


@pytest.mark.asyncio
async def test_manual_reconnect_closes_old_connection_and_stores_idle_generation(pool, monkeypatch):
    server = _server()
    key = pool._create_connection_key(server)
    old = PooledConnection(_Connection(), key, 1)
    pool.connections[key] = old
    connection = _Connection()
    close = AsyncMock()
    monkeypatch.setattr(pool, "_close_connection", close)
    monkeypatch.setattr(pool, "_reserve_capacity", AsyncMock(return_value=True))
    monkeypatch.setattr(pool, "_open_connection", AsyncMock(return_value=connection))

    success, result, message = await pool.manual_reconnect(server)

    assert success is True
    assert result is connection
    assert "Manual reconnection successful" in message
    close.assert_awaited_once_with(old)
    assert pool.connections[key].in_use_count == 0


@pytest.mark.asyncio
async def test_manual_reconnect_returns_capacity_failure(pool, monkeypatch):
    monkeypatch.setattr(pool, "_reserve_capacity", AsyncMock(return_value=False))

    success, connection, message = await pool.manual_reconnect(_server())

    assert success is False
    assert connection is None
    assert "at capacity" in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "message"),
    [
        (asyncssh.PermissionDenied("denied"), "Authentication failed"),
        (asyncio.TimeoutError(), "connection timeout"),
        (asyncssh.ConnectionLost("reset"), "SSH error: reset"),
        (ValueError("unsupported"), "unsupported"),
        (RuntimeError("socket failed"), "Connection error: socket failed"),
    ],
)
async def test_manual_reconnect_maps_open_failures(pool, monkeypatch, error, message):
    abort = Mock()
    monkeypatch.setattr(pool, "_reserve_capacity", AsyncMock(return_value=True))
    monkeypatch.setattr(pool, "_open_connection", AsyncMock(side_effect=error))
    monkeypatch.setattr(pool, "_abort_open", abort)

    success, connection, detail = await pool.manual_reconnect(_server())

    assert success is False
    assert connection is None
    assert message.lower() in detail.lower()
    abort.assert_called_once_with(None)


@pytest.mark.asyncio
async def test_manual_reconnect_preserves_cancellation(pool, monkeypatch):
    abort = Mock()
    monkeypatch.setattr(pool, "_reserve_capacity", AsyncMock(return_value=True))
    monkeypatch.setattr(pool, "_open_connection", AsyncMock(side_effect=asyncio.CancelledError))
    monkeypatch.setattr(pool, "_abort_open", abort)

    with pytest.raises(asyncio.CancelledError):
        await pool.manual_reconnect(_server())
    abort.assert_called_once_with(None)


@pytest.mark.asyncio
async def test_release_checks_identity_and_remove_closes_connection(pool, monkeypatch):
    server = _server()
    key = pool._create_connection_key(server)
    connection = _Connection()
    pooled = PooledConnection(connection, key, 3)
    pooled.acquire()
    pool.connections[key] = pooled

    await pool.release_connection(server, _Connection())
    assert pooled.in_use_count == 1

    lease = ConnectionLease(pool, key, 3, connection)
    await pool.release_lease(lease)
    assert pooled.in_use_count == 0

    close = AsyncMock()
    monkeypatch.setattr(pool, "_close_connection", close)
    await pool.remove_connection(server)
    close.assert_awaited_once_with(pooled)
    assert key not in pool.connections
