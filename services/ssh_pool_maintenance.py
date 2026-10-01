"""Focused helpers; mutable state remains owned by the original controller."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Dict, List

from modules.models import Server
from services.ssh_pool_types import PooledConnection

if TYPE_CHECKING:
    from .ssh_connection_pool import SSHConnectionPool


logger = logging.getLogger("services.ssh_connection_pool")


async def _cleanup_loop(self: SSHConnectionPool):
    """Background task to clean up stale connections"""
    while True:
        try:
            await asyncio.sleep(self.cleanup_interval)
            await self._cleanup_stale_connections()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in cleanup loop: {e}")


async def _cleanup_stale_connections(self: SSHConnectionPool):
    """Remove stale, idle, or dead connections"""
    stale_connections: list[PooledConnection] = []
    async with self.pool_lock:
        now = time.time()
        to_remove: list[PooledConnection] = []

        for key, pooled_conn in list(self.connections.items()):
            # Check if connection is dead
            if not pooled_conn.is_alive():
                to_remove.append(pooled_conn)
                logger.debug(f"Removing dead connection: {key}")
                continue

            # Check idle timeout
            idle_time = now - pooled_conn.last_used
            if pooled_conn.in_use_count == 0 and idle_time > self.idle_timeout:
                to_remove.append(pooled_conn)
                logger.debug(f"Removing idle connection (idle {idle_time:.1f}s): {key}")
                continue

            # Check max lifetime
            age = now - pooled_conn.created_at
            if age > self.max_lifetime:
                to_remove.append(pooled_conn)
                logger.debug(f"Removing old connection (age {age:.1f}s): {key}")
                continue

        # Retire stale connections. Active leases keep their exact generation
        # alive until every holder releases it.
        for pooled_conn in to_remove:
            close_candidate = self._retire_connection_locked(pooled_conn)
            if close_candidate is not None:
                stale_connections.append(close_candidate)

        if to_remove:
            logger.info(
                f"Cleaned up {len(to_remove)} stale connection(s). Active: {len(self.connections)}"
            )

    if stale_connections:
        await self._close_connections_safely(stale_connections)


async def _close_connections_safely(
    self: SSHConnectionPool, connections: List[PooledConnection]
) -> None:
    """Close pool-owned connections fully before propagating cancellation."""
    unique_connections = list({id(connection): connection for connection in connections}.values())
    if not unique_connections:
        return

    close_future = asyncio.gather(
        *(connection.close() for connection in unique_connections),
        return_exceptions=True,
    )
    try:
        await asyncio.shield(close_future)
    except asyncio.CancelledError:
        await close_future
        raise


async def get_pool_stats(self: SSHConnectionPool) -> Dict:
    """Get statistics about the connection pool"""
    async with self.pool_lock:
        active = list(self.connections.values())
        draining = list(self._draining_connections.values())
        total = len(active)
        alive = sum(1 for pc in active if pc.is_alive())
        in_use = sum(1 for pc in active if pc.in_use_count > 0)
        leases = sum(pc.in_use_count for pc in active) + sum(pc.in_use_count for pc in draining)

        return {
            "total_connections": total,
            "alive_connections": alive,
            "in_use_connections": in_use,
            "idle_connections": alive - in_use,
            "active_leases": leases,
            "draining_connections": len(draining),
            "idle_timeout": self.idle_timeout,
            "max_lifetime": self.max_lifetime,
            "keepalive_interval": self.keepalive_interval,
            "keepalive_count_max": self.keepalive_count_max,
        }


async def get_connection_info(self: SSHConnectionPool, server: Server) -> dict:
    """
    Get connection information for a specific server

    Args:
        server: Server instance

    Returns:
        dict: Connection information including status, time, reconnection count
    """
    key = self._create_connection_key(server)

    async with self.pool_lock:
        if key in self.connections:
            pooled_conn = self.connections[key]
            now = time.time()
            window_start = now - self.max_lifetime

            # Count recent reconnection attempts
            recent_reconnections = [
                ts for ts in pooled_conn.reconnection_attempts if ts > window_start
            ]

            return {
                "connected": pooled_conn.is_alive(),
                "created_at": pooled_conn.created_at,
                "last_used": pooled_conn.last_used,
                "connection_age": now - pooled_conn.created_at,
                "idle_time": now - pooled_conn.last_used,
                "in_use": pooled_conn.in_use_count > 0,
                "active_leases": pooled_conn.in_use_count,
                "reconnection_count": len(recent_reconnections),
                "max_reconnections": self.max_reconnections_per_hour,
                "pooling_enabled": True,
                "connection_key": str(key),
            }
        else:
            return {
                "connected": False,
                "created_at": None,
                "last_used": None,
                "connection_age": None,
                "idle_time": None,
                "in_use": False,
                "active_leases": 0,
                "reconnection_count": 0,
                "max_reconnections": self.max_reconnections_per_hour,
                "pooling_enabled": True,
                "connection_key": str(key),
            }
