"""Focused helpers; mutable state remains owned by the original controller."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Optional, Tuple

import asyncssh

from modules.models import Server
from services.ssh_pool_types import ConnectionKey, PooledConnection

if TYPE_CHECKING:
    from .ssh_connection_pool import SSHConnectionPool

logger = logging.getLogger("services.ssh_connection_pool")


async def _open_reconnected(
    self: SSHConnectionPool, server: Server, key: ConnectionKey, reconnection_attempts: list[float]
) -> tuple[bool, asyncssh.SSHClientConnection | None, str]:
    try:
        conn = await self._open_connection(server)
        new_pooled_conn = PooledConnection(conn, key)
        new_pooled_conn.reconnection_attempts = reconnection_attempts
        self._record_reconnection(new_pooled_conn)
        new_pooled_conn.acquire()
        await self._register_acquired_connection(server, new_pooled_conn)
        return True, conn, "Reconnected successfully"
    except asyncssh.PermissionDenied:
        return False, None, "Authentication failed"
    except asyncio.TimeoutError:
        return (
            False,
            None,
            "SSH connection timeout - server may be unreachable or too slow to respond",
        )
    except asyncssh.Error as e:
        return False, None, f"SSH error: {str(e)}"
    except ValueError as e:
        return False, None, str(e)
    except Exception as e:
        return False, None, f"Connection error: {str(e)}"


async def _reconnect(
    self: SSHConnectionPool,
    server: Server,
    *,
    failed_connection: Optional[asyncssh.SSHClientConnection] = None,
) -> Tuple[bool, Optional[asyncssh.SSHClientConnection], str]:
    """Reconnect one target, optionally scoped to a failed generation."""
    key = self._create_connection_key(server)
    key_lock = await self._get_key_lock(key)

    async with key_lock:
        stale_connection = None
        async with self.pool_lock:
            active_connection = self.connections.get(key)
            failed_generation = self._find_connection_locked(failed_connection)

            # A concurrent holder may already have replaced the same failed
            # generation. Reuse that replacement instead of opening a third
            # connection and disrupting its active leases.
            if failed_connection is not None and failed_generation is not active_connection:
                if active_connection is not None and active_connection.is_alive():
                    connection_age = time.time() - active_connection.created_at
                    if connection_age <= self.max_lifetime:
                        active_connection.acquire()
                        return (
                            True,
                            active_connection.conn,
                            "Reconnected successfully",
                        )

            rate_limit_source = active_connection or failed_generation
            if rate_limit_source is not None:
                can_reconnect, limit_msg = self._can_reconnect(rate_limit_source)
                if not can_reconnect:
                    return False, None, limit_msg
                reconnection_attempts = rate_limit_source.reconnection_attempts.copy()
            else:
                reconnection_attempts = []

            if active_connection is not None:
                stale_connection = self._retire_connection_locked(active_connection)

        if stale_connection is not None:
            await self._close_connections_safely([stale_connection])

        return await _open_reconnected(self, server, key, reconnection_attempts)


async def manual_reconnect(
    self: SSHConnectionPool, server: Server
) -> Tuple[bool, Optional[asyncssh.SSHClientConnection], str]:
    """
    Manually force reconnection for a server without rate limiting.
    This is used for user-initiated reconnection from WebUI.
    Resets the reconnection counter after successful connection.

    Args:
        server: Server instance

    Returns:
        Tuple[bool, Optional[connection], str]: (success, connection, message)
    """
    key = self._create_connection_key(server)
    key_lock = await self._get_key_lock(key)

    async with key_lock:
        stale_connection = None
        async with self.pool_lock:
            pooled_conn = self.connections.get(key)
            if pooled_conn is not None:
                stale_connection = self._retire_connection_locked(pooled_conn)
        if stale_connection is not None:
            await self._close_connections_safely([stale_connection])

        try:
            conn = await self._open_connection(server)
            new_pooled_conn = PooledConnection(conn, key)
            new_pooled_conn.acquire()
            await self._register_acquired_connection(server, new_pooled_conn)
            return (
                True,
                conn,
                "手动重连成功，计数已重置 | Manual reconnection successful, counter reset",
            )
        except asyncssh.PermissionDenied:
            return False, None, "认证失败 | Authentication failed"
        except asyncio.TimeoutError:
            return (
                False,
                None,
                "连接超时 - 服务器可能无法访问或响应过慢 | SSH connection timeout - server may be unreachable or too slow to respond",
            )
        except asyncssh.Error as e:
            return False, None, f"SSH错误 | SSH error: {str(e)}"
        except ValueError as e:
            return False, None, str(e)
        except Exception as e:
            return False, None, f"连接错误 | Connection error: {str(e)}"


async def reset_reconnection_counter(self: SSHConnectionPool, server: Server) -> Tuple[bool, str]:
    """
    Reset the reconnection counter for a server without reconnecting.

    Args:
        server: Server instance

    Returns:
        Tuple[bool, str]: (success, message)
    """
    key = self._create_connection_key(server)

    async with self.pool_lock:
        pooled_conn = self.connections.get(key)

        if pooled_conn:
            old_count = len(pooled_conn.reconnection_attempts)
            pooled_conn.reconnection_attempts = []
            logger.info(f"[SSH Pool] Reset reconnection counter for {key}: {old_count} -> 0")
            return (
                True,
                f"重连计数已重置 (从 {old_count} 重置为 0) | Reconnection counter reset (from {old_count} to 0)",
            )
        else:
            logger.info(f"[SSH Pool] No connection found for {key}, nothing to reset")
            return True, "无活动连接，无需重置 | No active connection, nothing to reset"
