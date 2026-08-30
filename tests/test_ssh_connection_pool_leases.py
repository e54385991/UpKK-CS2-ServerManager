import asyncio
import gc
import importlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes.actions import status as action_status
from modules.models import AuthType
from services.ssh import connection as connection_module
from services.ssh_connection_pool import (
    PooledConnection,
    SSHConnectionPool,
    ssh_connection_pool,
)
from services.ssh_manager import SSHManager


class FakeSSHConnection:
    def __init__(self, generation: int):
        self.generation = generation
        self.closed = False
        self.close_calls = 0

    def is_closed(self):
        return self.closed

    def close(self):
        self.close_calls += 1
        self.closed = True

    async def wait_closed(self):
        return None


def server_fixture(host: str = "pool.example", server_id: int = 41):
    return SimpleNamespace(
        id=server_id,
        host=host,
        ssh_port=22,
        ssh_user="cs2",
        auth_type=AuthType.PASSWORD,
        is_password_auth=True,
        is_key_auth=False,
        ssh_password="test",
        ssh_key_path=None,
        is_ssh_down=False,
    )


def isolated_pool(**kwargs) -> SSHConnectionPool:
    previous_instance = SSHConnectionPool._instance
    SSHConnectionPool._instance = None
    try:
        return SSHConnectionPool(**kwargs)
    finally:
        SSHConnectionPool._instance = previous_instance


@pytest.mark.asyncio
async def test_same_key_concurrent_acquires_open_only_one_connection(monkeypatch):
    pool = isolated_pool()
    server = server_fixture()
    open_started = asyncio.Event()
    allow_open = asyncio.Event()
    open_calls = 0

    async def open_connection(_server):
        nonlocal open_calls
        open_calls += 1
        open_started.set()
        await allow_open.wait()
        return FakeSSHConnection(open_calls)

    monkeypatch.setattr(pool, "_open_connection", open_connection)

    first_task = asyncio.create_task(pool.get_connection(server))
    await open_started.wait()
    second_task = asyncio.create_task(pool.get_connection(server))
    await asyncio.sleep(0)
    assert open_calls == 1

    allow_open.set()
    first_result, second_result = await asyncio.gather(first_task, second_task)

    assert first_result[0] is True
    assert second_result[0] is True
    assert first_result[1] is second_result[1]
    assert open_calls == 1

    connection = first_result[1]
    await pool.release_connection(server, connection)
    await pool.release_connection(server, connection)
    await pool.close_all()


@pytest.mark.asyncio
async def test_explicit_lease_always_releases_exact_generation(monkeypatch):
    pool = isolated_pool()
    server = server_fixture()
    connection = FakeSSHConnection(1)
    monkeypatch.setattr(pool, "_open_connection", AsyncMock(return_value=connection))

    with pytest.raises(RuntimeError, match="operation failed"):
        async with pool.lease(server) as leased:
            assert leased is connection
            key = pool._create_connection_key(server)
            assert pool.connections[key].in_use_count == 1
            raise RuntimeError("operation failed")

    key = pool._create_connection_key(server)
    assert pool.connections[key].in_use_count == 0
    await pool.close_all()


@pytest.mark.asyncio
async def test_expired_generation_drains_without_touching_new_lease(monkeypatch):
    pool = isolated_pool(max_lifetime=10)
    server = server_fixture()
    opened = []

    async def open_connection(_server):
        connection = FakeSSHConnection(len(opened) + 1)
        opened.append(connection)
        return connection

    monkeypatch.setattr(pool, "_open_connection", open_connection)
    monkeypatch.setattr(connection_module, "ssh_connection_pool", pool)
    monkeypatch.setattr(connection_module, "_schedule_status_update", lambda *_args: None)

    old_manager = SSHManager()
    new_manager = SSHManager()
    assert (await old_manager.connect(server))[0] is True
    old_connection = old_manager.conn
    key = pool._create_connection_key(server)
    old_generation = pool.connections[key]
    old_generation.created_at = time.time() - pool.max_lifetime - 1

    assert (await new_manager.connect(server))[0] is True
    new_connection = new_manager.conn
    new_generation = pool.connections[key]

    assert old_connection is not new_connection
    assert old_generation.draining is True
    assert old_generation.in_use_count == 1
    assert old_connection.closed is False
    assert new_generation.in_use_count == 1

    await old_manager.disconnect()

    assert old_connection.closed is True
    assert old_connection.close_calls == 1
    assert new_generation.in_use_count == 1
    assert new_connection.closed is False
    assert not pool._draining_connections

    await new_manager.disconnect()
    assert new_generation.in_use_count == 0
    await pool.close_all()


@pytest.mark.asyncio
async def test_concurrent_reconnects_of_old_generation_reuse_one_replacement(monkeypatch):
    pool = isolated_pool()
    server = server_fixture()
    opened = []

    async def open_connection(_server):
        connection = FakeSSHConnection(len(opened) + 1)
        opened.append(connection)
        return connection

    monkeypatch.setattr(pool, "_open_connection", open_connection)

    first = await pool.get_connection(server)
    second = await pool.get_connection(server)
    old_connection = first[1]
    assert second[1] is old_connection

    first_reconnect = await pool.reconnect_for_connection(server, old_connection)
    second_reconnect = await pool.reconnect_for_connection(server, old_connection)
    new_connection = first_reconnect[1]

    assert first_reconnect[0] is True
    assert second_reconnect[0] is True
    assert second_reconnect[1] is new_connection
    assert len(opened) == 2

    key = pool._create_connection_key(server)
    new_generation = pool.connections[key]
    assert new_generation.in_use_count == 2
    assert old_connection.closed is False

    await pool.release_connection(server, old_connection)
    assert old_connection.closed is False
    assert new_generation.in_use_count == 2
    await pool.release_connection(server, old_connection)
    assert old_connection.closed is True
    assert new_generation.in_use_count == 2

    await pool.release_connection(server, new_connection)
    await pool.release_connection(server, new_connection)
    assert new_generation.in_use_count == 0
    await pool.close_all()


@pytest.mark.asyncio
async def test_managers_transfer_their_exact_leases_during_reconnect(monkeypatch):
    pool = isolated_pool()
    server = server_fixture()
    opened = []

    async def open_connection(_server):
        connection = FakeSSHConnection(len(opened) + 1)
        opened.append(connection)
        return connection

    monkeypatch.setattr(pool, "_open_connection", open_connection)
    monkeypatch.setattr(connection_module, "ssh_connection_pool", pool)
    monkeypatch.setattr(connection_module, "_schedule_status_update", lambda *_args: None)

    first_manager = SSHManager()
    second_manager = SSHManager()
    assert (await first_manager.connect(server))[0] is True
    assert (await second_manager.connect(server))[0] is True
    old_connection = first_manager.conn

    assert (await first_manager._reconnect_current_pooled_connection(server))[0] is True
    replacement = first_manager.conn
    assert old_connection.closed is False

    assert (await second_manager._reconnect_current_pooled_connection(server))[0] is True
    assert second_manager.conn is replacement
    assert len(opened) == 2
    assert old_connection.closed is True

    key = pool._create_connection_key(server)
    replacement_generation = pool.connections[key]
    assert replacement_generation.in_use_count == 2

    await first_manager.disconnect()
    assert replacement_generation.in_use_count == 1
    assert replacement.closed is False
    await second_manager.disconnect()
    assert replacement_generation.in_use_count == 0
    await pool.close_all()


@pytest.mark.asyncio
async def test_key_locks_are_reclaimed_after_failed_target_churn(monkeypatch):
    pool = isolated_pool()

    async def fail_connection(_server):
        raise OSError("unreachable")

    monkeypatch.setattr(pool, "_open_connection", fail_connection)

    for server_id in range(200):
        result = await pool.get_connection(
            server_fixture(host=f"failed-{server_id}.example", server_id=server_id)
        )
        assert result[0] is False

    gc.collect()
    assert len(pool._key_locks) == 0
    await pool.close_all()


@pytest.mark.asyncio
async def test_cancelled_release_still_returns_exact_lease(monkeypatch):
    pool = isolated_pool()
    server = server_fixture()
    connection = FakeSSHConnection(1)

    monkeypatch.setattr(pool, "_open_connection", AsyncMock(return_value=connection))
    assert (await pool.get_connection(server))[0] is True

    async with pool.pool_lock:
        release_task = asyncio.create_task(pool.release_connection(server, connection))
        await asyncio.sleep(0)
        release_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await release_task

    key = pool._create_connection_key(server)
    assert pool.connections[key].in_use_count == 0
    await pool.close_all()


@pytest.mark.asyncio
async def test_cancelled_registration_closes_unowned_raw_connection():
    pool = isolated_pool()
    server = server_fixture()
    connection = FakeSSHConnection(1)
    key = pool._create_connection_key(server)
    pooled_connection = PooledConnection(connection, key)
    pooled_connection.acquire()

    async with pool.pool_lock:
        registration = asyncio.create_task(
            pool._register_acquired_connection(server, pooled_connection)
        )
        await asyncio.sleep(0)
        registration.cancel()

    with pytest.raises(asyncio.CancelledError):
        await registration

    assert connection.closed is True
    assert key not in pool.connections
    assert not pool._connection_index


@pytest.mark.asyncio
async def test_manual_reconnect_endpoint_releases_returned_lease(monkeypatch):
    server = server_fixture()
    connection = FakeSSHConnection(1)
    db = SimpleNamespace(
        execute=AsyncMock(),
        commit=AsyncMock(),
    )
    user = object()

    monkeypatch.setattr(
        action_status,
        "get_server_and_verify_ownership",
        AsyncMock(return_value=server),
    )
    reconnect = AsyncMock(return_value=(True, connection, "reconnected"))
    release = AsyncMock()
    monkeypatch.setattr(ssh_connection_pool, "manual_reconnect", reconnect)
    monkeypatch.setattr(ssh_connection_pool, "release_connection", release)

    result = await action_status.reconnect_ssh(41, db=db, current_user=user)

    assert result == {"success": True, "message": "reconnected"}
    reconnect.assert_awaited_once_with(server)
    release.assert_awaited_once_with(server, connection)


@pytest.mark.asyncio
async def test_open_connection_enables_ssh_keepalive(monkeypatch):
    pool = isolated_pool()
    captured = {}

    async def fake_connect(**kwargs):
        captured.update(kwargs)
        return FakeSSHConnection(1)

    monkeypatch.setattr(
        importlib.import_module("services.ssh_connection_pool").asyncssh,
        "connect",
        fake_connect,
    )
    await pool._open_connection(server_fixture())
    assert captured["keepalive_interval"] == 30
    assert captured["keepalive_count_max"] == 3
    await pool.close_all()


@pytest.mark.asyncio
async def test_pool_stats_include_leases_and_keepalive(monkeypatch):
    pool = isolated_pool()
    server = server_fixture()
    connection = FakeSSHConnection(1)
    monkeypatch.setattr(pool, "_open_connection", AsyncMock(return_value=connection))

    success, leased, _message = await pool.get_connection(server)
    assert success is True
    stats = await pool.get_pool_stats()
    assert stats["alive_connections"] == 1
    assert stats["in_use_connections"] == 1
    assert stats["active_leases"] == 1
    assert stats["idle_timeout"] == 900
    assert stats["keepalive_interval"] == 30

    info = await pool.get_connection_info(server)
    assert info["connected"] is True
    assert info["active_leases"] == 1

    await pool.release_connection(server, leased)
    await pool.close_all()
