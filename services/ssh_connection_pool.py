"""
SSH Connection Pool for connection reuse and management
Optimizes SSH operations by sharing connections for the same host
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import asyncssh

from modules.models import AuthType, Server
from services.ssh_host_keys import server_pinned_host_key_options

logger = logging.getLogger(__name__)


class ConnectionKey:
    """Unique key for identifying SSH connections"""

    def __init__(
        self,
        server_id: int,
        host: str,
        port: int,
        user: str,
        auth_type: AuthType,
        credential_revision: int = 0,
    ):
        self.server_id = server_id
        self.host = host
        self.port = port
        self.user = user
        self.auth_type = auth_type
        self.credential_revision = credential_revision

    def __hash__(self):
        return hash(
            (
                self.server_id,
                self.host,
                self.port,
                self.user,
                self.auth_type,
                self.credential_revision,
            )
        )

    def __eq__(self, other):
        if not isinstance(other, ConnectionKey):
            return False
        return (
            self.server_id == other.server_id
            and self.host == other.host
            and self.port == other.port
            and self.user == other.user
            and self.auth_type == other.auth_type
            and self.credential_revision == other.credential_revision
        )

    def __repr__(self):
        return (
            f"ConnectionKey(server={self.server_id}, {self.user}@{self.host}:{self.port}, "
            f"{self.auth_type}, revision={self.credential_revision})"
        )


class PooledConnection:
    """Wrapper for a pooled SSH connection"""

    def __init__(
        self,
        conn: asyncssh.SSHClientConnection,
        key: ConnectionKey,
        generation: int,
    ):
        self.conn = conn
        self.key = key
        self.generation = generation
        self.created_at = time.time()
        self.last_used = time.time()
        self.in_use_count = 0
        self.reconnection_attempts: List[float] = []  # Timestamps of reconnection attempts

    def is_alive(self) -> bool:
        """Check if connection is still alive"""
        return self.conn is not None and not self.conn.is_closed()

    def mark_used(self):
        """Mark connection as used"""
        self.last_used = time.time()

    def acquire(self):
        """
        Mark connection as in-use

        Note: This is a synchronous method (not async) because it only updates
        simple counter/timestamp fields. It's called while already holding the
        pool's async lock, so it doesn't need its own async operations.
        """
        self.in_use_count += 1
        self.mark_used()

    def release(self):
        """
        Mark connection as released

        Note: This is a synchronous method (not async) because it only updates
        a simple counter field. It's called while already holding the pool's
        async lock, so it doesn't need its own async operations.
        """
        self.in_use_count = max(0, self.in_use_count - 1)

    async def close(self):
        """Close the connection"""
        if self.conn and not self.conn.is_closed():
            self.conn.close()
            await self.conn.wait_closed()
        self.conn = None


@dataclass(slots=True)
class ConnectionLease:
    """One generation-bound checkout from the SSH connection pool."""

    pool: "SSHConnectionPool"
    key: ConnectionKey
    generation: int
    connection: asyncssh.SSHClientConnection
    released: bool = False

    async def release(self) -> None:
        if self.released:
            return
        self.released = True
        await self.pool.release_lease(self)

    async def __aenter__(self) -> asyncssh.SSHClientConnection:
        return self.connection

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        await self.release()


class SSHConnectionPool:
    """
    SSH Connection Pool for managing and reusing SSH connections

    Features:
    - Connection reuse for same host/user/auth combination
    - Automatic connection health checking
    - Configurable idle timeout and max lifetime
    - Thread-safe connection management
    - Automatic reconnection with rate limiting
    """

    # Singleton instance
    _instance = None

    def __new__(cls, *_args, shared: bool = True, **_kwargs):
        if not shared:
            instance = super().__new__(cls)
            instance._initialized = False
            return instance
        # Simple singleton without locks - Python's GIL ensures thread safety for instance creation
        # Multiple calls will see the same _instance after first creation
        if cls._instance is None:
            cls._instance = super(SSHConnectionPool, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        idle_timeout: int = 300,  # 5 minutes
        max_lifetime: int = 3600,  # 1 hour
        cleanup_interval: int = 60,  # 1 minute
        max_reconnections_per_hour: int = 10,
        max_connections: int = 50,
        acquire_timeout: float = 15.0,
        shared: bool = True,
    ):  # Max reconnections per hour
        """
        Initialize connection pool

        Args:
            idle_timeout: Close connections idle for this many seconds
            max_lifetime: Close connections older than this many seconds
            cleanup_interval: Run cleanup every N seconds
            max_reconnections_per_hour: Maximum reconnection attempts per hour per connection
        """
        del shared
        if self._initialized:
            return

        self._initialized = True
        self.idle_timeout = idle_timeout
        self.max_lifetime = max_lifetime
        self.cleanup_interval = cleanup_interval
        self.max_reconnections_per_hour = max_reconnections_per_hour
        self.max_connections = max_connections
        self.acquire_timeout = acquire_timeout
        self._generation = 0
        self._capacity = asyncio.BoundedSemaphore(max_connections)

        # Connection storage: ConnectionKey -> PooledConnection
        self.connections: Dict[ConnectionKey, PooledConnection] = {}
        self.pool_lock = asyncio.Lock()
        self._key_locks: Dict[ConnectionKey, asyncio.Lock] = {}

        # Cleanup task
        self.cleanup_task: Optional[asyncio.Task] = None

        logger.info(
            f"SSH Connection Pool initialized: "
            f"idle_timeout={idle_timeout}s, max_lifetime={max_lifetime}s, "
            f"max_reconnections_per_hour={max_reconnections_per_hour}, "
            f"max_connections={max_connections}"
        )

    async def start_cleanup(self):
        """Start the background cleanup task"""
        if self.cleanup_task is None or self.cleanup_task.done():
            self.cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("Started SSH connection pool cleanup task")

    async def stop_cleanup(self):
        """Stop the background cleanup task"""
        if self.cleanup_task and not self.cleanup_task.done():
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
            logger.info("Stopped SSH connection pool cleanup task")

    async def _cleanup_loop(self):
        """Background task to clean up stale connections"""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self._cleanup_stale_connections()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")

    async def _cleanup_stale_connections(self):
        """Remove stale, idle, or dead connections"""
        stale_connections: list[PooledConnection] = []
        async with self.pool_lock:
            now = time.time()
            to_remove = []

            for key, pooled_conn in self.connections.items():
                # Check if connection is dead
                if not pooled_conn.is_alive():
                    to_remove.append(key)
                    logger.debug(f"Removing dead connection: {key}")
                    continue

                # Don't clean up connections in use
                if pooled_conn.in_use_count > 0:
                    continue

                # Check idle timeout
                idle_time = now - pooled_conn.last_used
                if idle_time > self.idle_timeout:
                    to_remove.append(key)
                    logger.debug(f"Removing idle connection (idle {idle_time:.1f}s): {key}")
                    continue

                # Check max lifetime
                age = now - pooled_conn.created_at
                if age > self.max_lifetime:
                    to_remove.append(key)
                    logger.debug(f"Removing old connection (age {age:.1f}s): {key}")
                    continue

            # Remove stale connections
            for key in to_remove:
                pooled_conn = self.connections.pop(key, None)
                if pooled_conn:
                    stale_connections.append(pooled_conn)

            if to_remove:
                logger.info(
                    f"Cleaned up {len(to_remove)} stale connection(s). "
                    f"Active: {len(self.connections)}"
                )

        if stale_connections:
            await asyncio.gather(
                *(self._close_connection(connection) for connection in stale_connections),
                return_exceptions=True,
            )

        async with self.pool_lock:
            for key, key_lock in tuple(self._key_locks.items()):
                if key not in self.connections and not key_lock.locked():
                    self._key_locks.pop(key, None)

    def _create_connection_key(self, server: Server) -> ConnectionKey:
        """Create a connection key from server configuration"""
        return ConnectionKey(
            server_id=int(server.id),
            host=server.host,
            port=server.ssh_port,
            user=server.ssh_user,
            auth_type=server.auth_type,
            credential_revision=int(getattr(server, "credential_revision", 0) or 0),
        )

    async def _get_key_lock(self, key: ConnectionKey) -> asyncio.Lock:
        """Return a per-target lock while holding the global map lock only briefly."""
        async with self.pool_lock:
            return self._key_locks.setdefault(key, asyncio.Lock())

    def _next_generation(self) -> int:
        self._generation += 1
        return self._generation

    async def _reserve_capacity(self) -> bool:
        try:
            await asyncio.wait_for(self._capacity.acquire(), timeout=self.acquire_timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def _close_connection(self, pooled_conn: PooledConnection) -> None:
        try:
            await pooled_conn.close()
        finally:
            self._capacity.release()

    def _abort_open(self, connection: Optional[asyncssh.SSHClientConnection]) -> None:
        """Release reserved capacity and initiate close after a failed/cancelled open."""
        if connection is not None and not connection.is_closed():
            connection.close()
        self._capacity.release()

    async def _open_connection(self, server: Server) -> asyncssh.SSHClientConnection:
        """Open one SSH connection without holding any pool-wide lock."""
        common = {
            "host": server.host,
            "port": server.ssh_port,
            "username": server.ssh_user,
            "connect_timeout": 15,
            **server_pinned_host_key_options(server),
        }
        if server.is_password_auth:
            return await asyncssh.connect(password=server.ssh_password, **common)
        if server.is_key_auth:
            return await asyncssh.connect(client_keys=[server.ssh_key_path], **common)
        raise ValueError(f"Unsupported auth type: {server.auth_type}")

    def _can_reconnect(self, pooled_conn: PooledConnection) -> Tuple[bool, str]:
        """
        Check if reconnection is allowed based on rate limiting

        The time window is based on the connection pool's max_lifetime setting,
        not a fixed 1 hour period.

        Args:
            pooled_conn: The pooled connection to check

        Returns:
            Tuple[bool, str]: (can_reconnect, message)
        """
        now = time.time()
        window_start = now - self.max_lifetime

        # Clean up old reconnection attempts (older than max_lifetime window)
        pooled_conn.reconnection_attempts = [
            ts for ts in pooled_conn.reconnection_attempts if ts > window_start
        ]

        # Check if we've exceeded the limit
        if len(pooled_conn.reconnection_attempts) >= self.max_reconnections_per_hour:
            oldest_attempt = min(pooled_conn.reconnection_attempts)
            time_until_reset = int(oldest_attempt + self.max_lifetime - now)
            window_minutes = int(self.max_lifetime / 60)
            return False, (
                f"已达到重连次数上限 ({self.max_reconnections_per_hour}次/{window_minutes}分钟)，"
                f"请等待 {time_until_reset} 秒后重试 | "
                f"Reconnection limit reached ({self.max_reconnections_per_hour}/{window_minutes} minutes), "
                f"please wait {time_until_reset} seconds"
            )

        return True, ""

    def _record_reconnection(self, pooled_conn: PooledConnection):
        """Record a reconnection attempt"""
        now = time.time()
        pooled_conn.reconnection_attempts.append(now)

        window_minutes = int(self.max_lifetime / 60)
        logger.info(
            f"[SSH Pool] Reconnection recorded for {pooled_conn.key}. "
            f"Total attempts in last {window_minutes} minutes: {len(pooled_conn.reconnection_attempts)}/{self.max_reconnections_per_hour}"
        )

    async def get_connection(
        self, server: Server
    ) -> Tuple[bool, Optional[asyncssh.SSHClientConnection], str]:
        """
        Get or create a connection for the server

        Args:
            server: Server instance

        Returns:
            Tuple[bool, Optional[connection], str]: (success, connection, message)
        """
        # Early validation: Check if server is marked as SSH down
        # This prevents repeated connection attempts to offline servers
        if server.is_ssh_down:
            logger.warning(
                f"[SSH Pool] Server {server.id} ({server.host}) is marked as SSH down. "
                f"Skipping connection attempt to prevent resource exhaustion."
            )
            return (
                False,
                None,
                (
                    "服务器已标记为离线（SSH连接失败多次）。请检查服务器状态后手动重连。 | "
                    "Server marked as offline (SSH connection failed multiple times). "
                    "Please check server status and manually reconnect."
                ),
            )

        key = self._create_connection_key(server)
        key_lock = await self._get_key_lock(key)

        async with key_lock:
            stale_connection = None
            async with self.pool_lock:
                pooled_conn = self.connections.get(key)
                if pooled_conn is not None:
                    connection_age = time.time() - pooled_conn.created_at
                    if pooled_conn.is_alive() and connection_age <= self.max_lifetime:
                        pooled_conn.acquire()
                        logger.debug(f"Reusing existing connection: {key}")
                        return True, pooled_conn.conn, "Reused existing connection"
                    stale_connection = self.connections.pop(key, None)

            if stale_connection is not None:
                await self._close_connection(stale_connection)

            if not await self._reserve_capacity():
                return (
                    False,
                    None,
                    f"SSH connection pool is at capacity ({self.max_connections})",
                )

            conn = None
            try:
                logger.debug(f"Creating new SSH connection: {key}")
                conn = await self._open_connection(server)
                pooled_conn = PooledConnection(conn, key, self._next_generation())
                pooled_conn.acquire()
                async with self.pool_lock:
                    self.connections[key] = pooled_conn
                    total = len(self.connections)
                logger.info(f"Created new SSH connection: {key}. Total connections: {total}")
                return True, conn, "Connected successfully"
            except asyncio.CancelledError:
                self._abort_open(conn)
                raise
            except asyncssh.PermissionDenied:
                self._abort_open(conn)
                return False, None, "Authentication failed"
            except asyncio.TimeoutError:
                self._abort_open(conn)
                return (
                    False,
                    None,
                    "SSH connection timeout - server may be unreachable or too slow to respond",
                )
            except asyncssh.Error as e:
                self._abort_open(conn)
                return False, None, f"SSH error: {str(e)}"
            except ValueError as e:
                self._abort_open(conn)
                return False, None, str(e)
            except Exception as e:
                self._abort_open(conn)
                return False, None, f"Connection error: {str(e)}"

    async def acquire_lease(self, server: Server) -> Tuple[bool, Optional[ConnectionLease], str]:
        """Acquire a generation-bound lease while preserving the legacy tuple API."""
        success, connection, message = await self.get_connection(server)
        if not success or connection is None:
            return False, None, message

        key = self._create_connection_key(server)
        async with self.pool_lock:
            pooled_conn = self.connections.get(key)
            if pooled_conn is None or pooled_conn.conn is not connection:
                return False, None, "SSH connection changed while acquiring lease"
            lease = ConnectionLease(
                pool=self,
                key=key,
                generation=pooled_conn.generation,
                connection=connection,
            )
        return True, lease, message

    async def reconnect(
        self, server: Server
    ) -> Tuple[bool, Optional[asyncssh.SSHClientConnection], str]:
        """
        Force reconnection for a server (close existing and create new)

        This method is used when SSH operations fail due to stale connections.
        It includes rate limiting to prevent infinite reconnection loops.

        Args:
            server: Server instance

        Returns:
            Tuple[bool, Optional[connection], str]: (success, connection, message)
        """
        key = self._create_connection_key(server)
        key_lock = await self._get_key_lock(key)

        async with key_lock:
            async with self.pool_lock:
                pooled_conn = self.connections.get(key)
                if pooled_conn is not None:
                    can_reconnect, limit_msg = self._can_reconnect(pooled_conn)
                    if not can_reconnect:
                        return False, None, limit_msg
                    reconnection_attempts = pooled_conn.reconnection_attempts.copy()
                    self.connections.pop(key, None)
                else:
                    reconnection_attempts = []

            if pooled_conn is not None:
                await self._close_connection(pooled_conn)

            if not await self._reserve_capacity():
                return (
                    False,
                    None,
                    f"SSH connection pool is at capacity ({self.max_connections})",
                )

            conn = None
            try:
                conn = await self._open_connection(server)
                new_pooled_conn = PooledConnection(conn, key, self._next_generation())
                new_pooled_conn.reconnection_attempts = reconnection_attempts
                self._record_reconnection(new_pooled_conn)
                new_pooled_conn.acquire()
                async with self.pool_lock:
                    self.connections[key] = new_pooled_conn
                return True, conn, "Reconnected successfully"
            except asyncio.CancelledError:
                self._abort_open(conn)
                raise
            except asyncssh.PermissionDenied:
                self._abort_open(conn)
                return False, None, "Authentication failed"
            except asyncio.TimeoutError:
                self._abort_open(conn)
                return (
                    False,
                    None,
                    "SSH connection timeout - server may be unreachable or too slow to respond",
                )
            except asyncssh.Error as e:
                self._abort_open(conn)
                return False, None, f"SSH error: {str(e)}"
            except ValueError as e:
                self._abort_open(conn)
                return False, None, str(e)
            except Exception as e:
                self._abort_open(conn)
                return False, None, f"Connection error: {str(e)}"

    async def reconnect_lease(
        self,
        server: Server,
        previous: Optional[ConnectionLease] = None,
    ) -> Tuple[bool, Optional[ConnectionLease], str]:
        """Reconnect and return a lease bound to the new generation."""
        if previous is not None:
            previous.released = True
        success, connection, message = await self.reconnect(server)
        if not success or connection is None:
            return False, None, message

        key = self._create_connection_key(server)
        async with self.pool_lock:
            pooled_conn = self.connections.get(key)
            if pooled_conn is None or pooled_conn.conn is not connection:
                return False, None, "SSH connection changed while reconnecting"
            lease = ConnectionLease(
                pool=self,
                key=key,
                generation=pooled_conn.generation,
                connection=connection,
            )
        return True, lease, message

    async def manual_reconnect(
        self, server: Server
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
            async with self.pool_lock:
                pooled_conn = self.connections.pop(key, None)
            if pooled_conn is not None:
                await self._close_connection(pooled_conn)

            if not await self._reserve_capacity():
                return (
                    False,
                    None,
                    f"SSH connection pool is at capacity ({self.max_connections})",
                )

            conn = None
            try:
                conn = await self._open_connection(server)
                new_pooled_conn = PooledConnection(conn, key, self._next_generation())
                new_pooled_conn.mark_used()
                async with self.pool_lock:
                    self.connections[key] = new_pooled_conn
                return (
                    True,
                    conn,
                    "手动重连成功，计数已重置 | Manual reconnection successful, counter reset",
                )
            except asyncio.CancelledError:
                self._abort_open(conn)
                raise
            except asyncssh.PermissionDenied:
                self._abort_open(conn)
                return False, None, "认证失败 | Authentication failed"
            except asyncio.TimeoutError:
                self._abort_open(conn)
                return (
                    False,
                    None,
                    "连接超时 - 服务器可能无法访问或响应过慢 | SSH connection timeout - server may be unreachable or too slow to respond",
                )
            except asyncssh.Error as e:
                self._abort_open(conn)
                return False, None, f"SSH错误 | SSH error: {str(e)}"
            except ValueError as e:
                self._abort_open(conn)
                return False, None, str(e)
            except Exception as e:
                self._abort_open(conn)
                return False, None, f"连接错误 | Connection error: {str(e)}"

    async def reset_reconnection_counter(self, server: Server) -> Tuple[bool, str]:
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

    async def release_connection(
        self,
        server: Server,
        connection: Optional[asyncssh.SSHClientConnection] = None,
    ):
        """
        Release a connection back to the pool

        Args:
            server: Server instance
        """
        key = self._create_connection_key(server)

        async with self.pool_lock:
            if key in self.connections:
                pooled_conn = self.connections[key]
                if connection is not None and pooled_conn.conn is not connection:
                    logger.debug("Ignoring release for stale SSH generation: %s", key)
                    return
                pooled_conn.release()
                logger.debug(f"Released connection: {key}")

    async def release_lease(self, lease: ConnectionLease) -> None:
        """Release only the exact pooled connection generation in the lease."""
        async with self.pool_lock:
            pooled_conn = self.connections.get(lease.key)
            if pooled_conn is None or pooled_conn.generation != lease.generation:
                logger.debug("Ignoring release for stale SSH lease: %s", lease.key)
                return
            pooled_conn.release()

    async def remove_connection(self, server: Server):
        """
        Remove and close a connection from the pool

        Args:
            server: Server instance
        """
        key = self._create_connection_key(server)
        key_lock = await self._get_key_lock(key)
        async with key_lock:
            async with self.pool_lock:
                pooled_conn = self.connections.pop(key, None)
            if pooled_conn is not None:
                await self._close_connection(pooled_conn)
                logger.info(f"Removed connection: {key}")

    async def close_all(self):
        """Close all connections in the pool"""
        async with self.pool_lock:
            connections = list(self.connections.values())
            self.connections.clear()
            self._key_locks.clear()
        logger.info(f"Closing all {len(connections)} connections")
        await asyncio.gather(
            *(self._close_connection(connection) for connection in connections),
            return_exceptions=True,
        )
        logger.info("All connections closed")

    async def get_pool_stats(self) -> Dict:
        """Get statistics about the connection pool"""
        async with self.pool_lock:
            total = len(self.connections)
            alive = sum(1 for pc in self.connections.values() if pc.is_alive())
            in_use = sum(1 for pc in self.connections.values() if pc.in_use_count > 0)

            return {
                "total_connections": total,
                "alive_connections": alive,
                "in_use_connections": in_use,
                "idle_connections": alive - in_use,
                "idle_timeout": self.idle_timeout,
                "max_lifetime": self.max_lifetime,
                "max_connections": self.max_connections,
                "available_capacity": getattr(self._capacity, "_value", 0),
            }

    async def get_connection_info(self, server: Server) -> dict:
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
                    "reconnection_count": 0,
                    "max_reconnections": self.max_reconnections_per_hour,
                    "pooling_enabled": True,
                    "connection_key": str(key),
                }


# Global connection pool instance
ssh_connection_pool = SSHConnectionPool()
