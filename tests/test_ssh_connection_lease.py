"""Generation and capacity guarantees for pooled SSH connections."""

from types import SimpleNamespace

import pytest

from modules.models import AuthType
from services.ssh_connection_pool import ConnectionLease, SSHConnectionPool


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def _server(server_id: int, revision: int = 0):
    return SimpleNamespace(
        id=server_id,
        host="ssh.example",
        ssh_port=22,
        ssh_user="cs2",
        auth_type=AuthType.PASSWORD,
        credential_revision=revision,
        is_password_auth=True,
        is_key_auth=False,
        ssh_password="secret",
        ssh_key_path=None,
        is_ssh_down=False,
    )


@pytest.mark.asyncio
async def test_stale_lease_cannot_release_new_connection_generation(monkeypatch):
    previous_instance = SSHConnectionPool._instance
    SSHConnectionPool._instance = None
    pool = SSHConnectionPool()
    monkeypatch.setattr(pool, "_open_connection", lambda _server: _async_connection())

    try:
        success, lease, _message = await pool.acquire_lease(_server(1))
        assert success is True
        assert lease is not None

        stale_lease = ConnectionLease(
            pool=pool,
            key=lease.key,
            generation=lease.generation,
            connection=lease.connection,
        )
        success, new_connection, _message = await pool.reconnect(_server(1))
        assert success is True
        assert new_connection is not None

        await stale_lease.release()
        pooled = pool.connections[pool._create_connection_key(_server(1))]
        assert pooled.conn is new_connection
        assert pooled.in_use_count == 1
    finally:
        await pool.close_all()
        SSHConnectionPool._instance = previous_instance


@pytest.mark.asyncio
async def test_pool_key_separates_servers_and_credential_revisions():
    previous_instance = SSHConnectionPool._instance
    SSHConnectionPool._instance = None
    pool = SSHConnectionPool()
    try:
        assert pool._create_connection_key(_server(1)) != pool._create_connection_key(_server(2))
        assert pool._create_connection_key(_server(1)) != pool._create_connection_key(
            _server(1, revision=1)
        )
    finally:
        await pool.close_all()
        SSHConnectionPool._instance = previous_instance


@pytest.mark.asyncio
async def test_aborted_open_closes_socket_and_restores_capacity():
    previous_instance = SSHConnectionPool._instance
    SSHConnectionPool._instance = None
    pool = SSHConnectionPool(max_connections=1)
    connection = _FakeConnection()
    try:
        assert await pool._reserve_capacity() is True
        assert pool._capacity._value == 0

        pool._abort_open(connection)

        assert connection.closed is True
        assert pool._capacity._value == 1
    finally:
        await pool.close_all()
        SSHConnectionPool._instance = previous_instance


async def _async_connection() -> _FakeConnection:
    return _FakeConnection()
