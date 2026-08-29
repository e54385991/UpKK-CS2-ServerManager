"""
SSH Connection Pool for connection reuse and management
Optimizes SSH operations by sharing connections for the same host
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Dict, List, Optional, Tuple
from weakref import WeakValueDictionary

import asyncssh

from modules.models import AuthType, Server

logger = logging.getLogger(__name__)


class ConnectionKey:
    """Unique key for identifying SSH connections"""

    def __init__(self, host: str, port: int, user: str, auth_type: AuthType):
        self.host = host
        self.port = port
        self.user = user
        self.auth_type = auth_type

    def __hash__(self):
        return hash((self.host, self.port, self.user, self.auth_type))

    def __eq__(self, other):
        if not isinstance(other, ConnectionKey):
            return False
        return (
            self.host == other.host
            and self.port == other.port
            and self.user == other.user
            and self.auth_type == other.auth_type
        )

    def __repr__(self):
        return f"ConnectionKey({self.user}@{self.host}:{self.port}, {self.auth_type})"


class PooledConnection:
    """Wrapper for a pooled SSH connection"""

    def __init__(self, conn: asyncssh.SSHClientConnection, key: ConnectionKey):
        self.conn = conn
        self.key = key
        self.created_at = time.time()
        self.last_used = time.time()
        self.in_use_count = 0
        self.draining = False
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

    def __new__(cls, *_args, **_kwargs):
        # Simple singleton without locks - Python's GIL ensures thread safety for instance creation
        # Multiple calls will see the same _instance after first creation
        if cls._instance is None:
            cls._instance = super(SSHConnectionPool, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        idle_timeout: int = 900,  # 15 minutes — keep warm sockets for reuse
        max_lifetime: int = 3600,  # 1 hour
        cleanup_interval: int = 60,  # 1 minute
        max_reconnections_per_hour: int = 10,
        keepalive_interval: int = 30,
        keepalive_count_max: int = 3,
    ):  # Max reconnections per hour
        """
        Initialize connection pool

        Args:
            idle_timeout: Close connections idle for this many seconds
            max_lifetime: Close connections older than this many seconds
            cleanup_interval: Run cleanup every N seconds
            max_reconnections_per_hour: Maximum reconnection attempts per hour per connection
            keepalive_interval: SSH-level keepalive seconds (NAT/firewall friendly)
            keepalive_count_max: Missed keepalives before AsyncSSH drops the socket
        """
        if self._initialized:
            return

        self._initialized = True
        self.idle_timeout = idle_timeout
        self.max_lifetime = max_lifetime
        self.cleanup_interval = cleanup_interval
        self.max_reconnections_per_hour = max_reconnections_per_hour
        self.keepalive_interval = keepalive_interval
        self.keepalive_count_max = keepalive_count_max

        # Connection storage: ConnectionKey -> PooledConnection
        self.connections: Dict[ConnectionKey, PooledConnection] = {}
        self._draining_connections: Dict[int, PooledConnection] = {}
        self._connection_index: Dict[int, PooledConnection] = {}
        self.pool_lock = asyncio.Lock()
        self._key_locks: WeakValueDictionary[ConnectionKey, asyncio.Lock] = WeakValueDictionary()

        # Cleanup task
        self.cleanup_task: Optional[asyncio.Task] = None

        logger.info(
            f"SSH Connection Pool initialized: "
            f"idle_timeout={idle_timeout}s, max_lifetime={max_lifetime}s, "
            f"keepalive={keepalive_interval}s/{keepalive_count_max}, "
            f"max_reconnections_per_hour={max_reconnections_per_hour}"
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
                    f"Cleaned up {len(to_remove)} stale connection(s). "
                    f"Active: {len(self.connections)}"
                )

        if stale_connections:
            await self._close_connections_safely(stale_connections)

    def _create_connection_key(self, server: Server) -> ConnectionKey:
        """Create a connection key from server configuration"""
        return ConnectionKey(
            host=server.host, port=server.ssh_port, user=server.ssh_user, auth_type=server.auth_type
        )

    async def _get_key_lock(self, key: ConnectionKey) -> asyncio.Lock:
        """Return a per-target lock while holding the global map lock only briefly."""
        async with self.pool_lock:
            return self._key_locks.setdefault(key, asyncio.Lock())

    def _register_connection_locked(self, pooled_conn: PooledConnection) -> None:
        """Register a newly opened active connection while holding ``pool_lock``."""
        pooled_conn.draining = False
        self.connections[pooled_conn.key] = pooled_conn
        if pooled_conn.conn is not None:
            self._connection_index[id(pooled_conn.conn)] = pooled_conn

    def _forget_connection_locked(self, pooled_conn: PooledConnection) -> None:
        """Remove all pool-owned references to one connection generation."""
        if self.connections.get(pooled_conn.key) is pooled_conn:
            self.connections.pop(pooled_conn.key, None)
        self._draining_connections.pop(id(pooled_conn), None)
        if pooled_conn.conn is not None:
            connection_id = id(pooled_conn.conn)
            if self._connection_index.get(connection_id) is pooled_conn:
                self._connection_index.pop(connection_id, None)

    def _retire_connection_locked(
        self, pooled_conn: PooledConnection
    ) -> Optional[PooledConnection]:
        """Stop new leases and return the generation once it is safe to close."""
        if self.connections.get(pooled_conn.key) is pooled_conn:
            self.connections.pop(pooled_conn.key, None)
        pooled_conn.draining = True
        if pooled_conn.in_use_count > 0:
            self._draining_connections[id(pooled_conn)] = pooled_conn
            return None
        self._forget_connection_locked(pooled_conn)
        return pooled_conn

    def _find_connection_locked(
        self, connection: Optional[asyncssh.SSHClientConnection]
    ) -> Optional[PooledConnection]:
        """Resolve a raw AsyncSSH connection to its exact pool generation."""
        if connection is None:
            return None
        pooled_conn = self._connection_index.get(id(connection))
        if pooled_conn is None or pooled_conn.conn is not connection:
            return None
        return pooled_conn

    async def _close_connections_safely(
        self,
        connections: List[PooledConnection],
    ) -> None:
        """Close pool-owned connections fully before propagating cancellation."""
        unique_connections = list(
            {id(connection): connection for connection in connections}.values()
        )
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

    async def _register_acquired_connection(
        self,
        server: Server,
        pooled_conn: PooledConnection,
    ) -> int:
        """Register a leased generation or clean it up if registration is cancelled."""
        registered = False
        try:
            async with self.pool_lock:
                self._register_connection_locked(pooled_conn)
                registered = True
                return len(self.connections)
        except BaseException:
            if registered:
                await self.release_connection(server, pooled_conn.conn)
            else:
                await self._close_connections_safely([pooled_conn])
            raise

    async def _open_connection(self, server: Server) -> asyncssh.SSHClientConnection:
        """Open one SSH connection without holding any pool-wide lock."""
        common = {
            "host": server.host,
            "port": server.ssh_port,
            "username": server.ssh_user,
            "known_hosts": None,
            "connect_timeout": 15,
            "keepalive_interval": self.keepalive_interval,
            "keepalive_count_max": self.keepalive_count_max,
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
                    stale_connection = self._retire_connection_locked(pooled_conn)

            if stale_connection is not None:
                await self._close_connections_safely([stale_connection])

            try:
                logger.debug(f"Creating new SSH connection: {key}")
                conn = await self._open_connection(server)
                pooled_conn = PooledConnection(conn, key)
                pooled_conn.acquire()
                total = await self._register_acquired_connection(server, pooled_conn)
                logger.info(f"Created new SSH connection: {key}. Total connections: {total}")
                return True, conn, "Connected successfully"
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

    @asynccontextmanager
    async def lease(self, server: Server):
        """Yield one exact connection generation and always release that lease."""
        success, connection, message = await self.get_connection(server)
        if not success or connection is None:
            raise ConnectionError(message)
        try:
            yield connection
        finally:
            await self.release_connection(server, connection)

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
        return await self._reconnect(server)

    async def reconnect_for_connection(
        self,
        server: Server,
        failed_connection: Optional[asyncssh.SSHClientConnection],
    ) -> Tuple[bool, Optional[asyncssh.SSHClientConnection], str]:
        """Reconnect the generation held by one SSHManager.

        If another holder already replaced that generation, lease the replacement
        instead of rotating the healthy new connection a second time.
        """
        return await self._reconnect(server, failed_connection=failed_connection)

    async def _reconnect(
        self,
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
            connection: Exact connection generation held by the caller. Legacy
                callers may omit this to release the current active generation.
        """
        release_task = asyncio.create_task(self._release_connection(server, connection))
        try:
            await asyncio.shield(release_task)
        except asyncio.CancelledError:
            # A cancelled request must not strand a draining generation. Finish
            # the small bookkeeping/close operation before propagating cancel.
            await release_task
            raise

    async def _release_connection(
        self,
        server: Server,
        connection: Optional[asyncssh.SSHClientConnection],
    ) -> None:
        """Perform exact-generation release for the cancellation-safe wrapper."""
        key = self._create_connection_key(server)
        close_candidate = None

        async with self.pool_lock:
            pooled_conn = (
                self._find_connection_locked(connection)
                if connection is not None
                else self.connections.get(key)
            )
            if pooled_conn is not None:
                pooled_conn.release()
                logger.debug(
                    "Released connection generation: %s (draining=%s, remaining=%s)",
                    key,
                    pooled_conn.draining,
                    pooled_conn.in_use_count,
                )
                if pooled_conn.draining and pooled_conn.in_use_count == 0:
                    self._forget_connection_locked(pooled_conn)
                    close_candidate = pooled_conn

        if close_candidate is not None:
            await self._close_connections_safely([close_candidate])

    async def remove_connection(self, server: Server):
        """
        Remove and close a connection from the pool

        Args:
            server: Server instance
        """
        key = self._create_connection_key(server)
        key_lock = await self._get_key_lock(key)
        async with key_lock:
            close_candidate = None
            async with self.pool_lock:
                pooled_conn = self.connections.get(key)
                if pooled_conn is not None:
                    close_candidate = self._retire_connection_locked(pooled_conn)
            if close_candidate is not None:
                await self._close_connections_safely([close_candidate])
                logger.info(f"Removed connection: {key}")

    async def close_all(self):
        """Close all connections in the pool"""
        async with self.pool_lock:
            connections = list(self.connections.values()) + list(
                self._draining_connections.values()
            )
            self.connections.clear()
            self._draining_connections.clear()
            self._connection_index.clear()
        logger.info(f"Closing all {len(connections)} connections")
        await self._close_connections_safely(connections)
        logger.info("All connections closed")

    async def get_pool_stats(self) -> Dict:
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


# Global connection pool instance
ssh_connection_pool = SSHConnectionPool()
