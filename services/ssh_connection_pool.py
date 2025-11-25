"""
SSH Connection Pool for connection reuse and management
Optimizes SSH operations by sharing connections for the same host
"""
import asyncssh
import asyncio
import time
from typing import Optional, Dict, Tuple, List
from datetime import datetime, timedelta
from modules.models import Server, AuthType
import logging

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
        return (self.host == other.host and 
                self.port == other.port and 
                self.user == other.user and 
                self.auth_type == other.auth_type)
    
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
    
    def __new__(cls):
        # Simple singleton without locks - Python's GIL ensures thread safety for instance creation
        # Multiple calls will see the same _instance after first creation
        if cls._instance is None:
            cls._instance = super(SSHConnectionPool, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, 
                 idle_timeout: int = 300,  # 5 minutes
                 max_lifetime: int = 3600,  # 1 hour
                 cleanup_interval: int = 60,  # 1 minute
                 max_reconnections_per_hour: int = 10):  # Max reconnections per hour
        """
        Initialize connection pool
        
        Args:
            idle_timeout: Close connections idle for this many seconds
            max_lifetime: Close connections older than this many seconds
            cleanup_interval: Run cleanup every N seconds
            max_reconnections_per_hour: Maximum reconnection attempts per hour per connection
        """
        if self._initialized:
            return
        
        self._initialized = True
        self.idle_timeout = idle_timeout
        self.max_lifetime = max_lifetime
        self.cleanup_interval = cleanup_interval
        self.max_reconnections_per_hour = max_reconnections_per_hour
        
        # Connection storage: ConnectionKey -> PooledConnection
        self.connections: Dict[ConnectionKey, PooledConnection] = {}
        self.pool_lock = asyncio.Lock()
        
        # Cleanup task
        self.cleanup_task: Optional[asyncio.Task] = None
        
        logger.info(
            f"SSH Connection Pool initialized: "
            f"idle_timeout={idle_timeout}s, max_lifetime={max_lifetime}s, "
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
                    logger.debug(
                        f"Removing idle connection (idle {idle_time:.1f}s): {key}"
                    )
                    continue
                
                # Check max lifetime
                age = now - pooled_conn.created_at
                if age > self.max_lifetime:
                    to_remove.append(key)
                    logger.debug(
                        f"Removing old connection (age {age:.1f}s): {key}"
                    )
                    continue
            
            # Remove stale connections
            for key in to_remove:
                pooled_conn = self.connections.pop(key, None)
                if pooled_conn:
                    await pooled_conn.close()
            
            if to_remove:
                logger.info(
                    f"Cleaned up {len(to_remove)} stale connection(s). "
                    f"Active: {len(self.connections)}"
                )
    
    def _create_connection_key(self, server: Server) -> ConnectionKey:
        """Create a connection key from server configuration"""
        return ConnectionKey(
            host=server.host,
            port=server.ssh_port,
            user=server.ssh_user,
            auth_type=server.auth_type
        )
    
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
    
    async def get_connection(self, server: Server) -> Tuple[bool, Optional[asyncssh.SSHClientConnection], str]:
        """
        Get or create a connection for the server
        
        Args:
            server: Server instance
        
        Returns:
            Tuple[bool, Optional[connection], str]: (success, connection, message)
        """
        key = self._create_connection_key(server)
        
        async with self.pool_lock:
            # Check if we have an existing connection
            if key in self.connections:
                pooled_conn = self.connections[key]
                
                # Check if connection has exceeded max lifetime
                # This is critical for avoiding long-running connection bugs
                now = time.time()
                connection_age = now - pooled_conn.created_at
                
                if connection_age > self.max_lifetime:
                    # Connection is too old, proactively reconnect
                    logger.info(
                        f"[SSH Pool] Connection exceeded max lifetime ({connection_age:.1f}s > {self.max_lifetime}s), "
                        f"reconnecting: {key}"
                    )
                    await pooled_conn.close()
                    del self.connections[key]
                    # Fall through to create new connection below
                elif pooled_conn.is_alive():
                    # Connection is still alive and within max lifetime
                    # Mark as in-use (simple counter update, already holding pool lock)
                    pooled_conn.acquire()
                    logger.debug(f"Reusing existing connection: {key}")
                    return True, pooled_conn.conn, "Reused existing connection"
                else:
                    # Connection is dead, remove it
                    logger.debug(f"Removing dead connection: {key}")
                    await pooled_conn.close()
                    del self.connections[key]
            
            # Create new connection
            try:
                logger.debug(f"Creating new SSH connection: {key}")
                
                if server.auth_type == AuthType.PASSWORD:
                    conn = await asyncssh.connect(
                        host=server.host,
                        port=server.ssh_port,
                        username=server.ssh_user,
                        password=server.ssh_password,
                        known_hosts=None
                    )
                elif server.auth_type == AuthType.KEY_FILE:
                    conn = await asyncssh.connect(
                        host=server.host,
                        port=server.ssh_port,
                        username=server.ssh_user,
                        client_keys=[server.ssh_key_path],
                        known_hosts=None
                    )
                else:
                    return False, None, f"Unsupported auth type: {server.auth_type}"
                
                # Store in pool
                pooled_conn = PooledConnection(conn, key)
                # Mark as in-use (simple counter update, already holding pool lock)
                pooled_conn.acquire()
                self.connections[key] = pooled_conn
                
                logger.info(
                    f"Created new SSH connection: {key}. "
                    f"Total connections: {len(self.connections)}"
                )
                
                return True, conn, "Connected successfully"
                
            except asyncssh.PermissionDenied:
                return False, None, "Authentication failed"
            except asyncssh.Error as e:
                return False, None, f"SSH error: {str(e)}"
            except Exception as e:
                return False, None, f"Connection error: {str(e)}"
    
    async def reconnect(self, server: Server) -> Tuple[bool, Optional[asyncssh.SSHClientConnection], str]:
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
        
        async with self.pool_lock:
            # Check if we have an existing connection
            pooled_conn = self.connections.get(key)
            
            # Preserve reconnection history for rate limiting
            reconnection_attempts = []
            if pooled_conn:
                reconnection_attempts = pooled_conn.reconnection_attempts.copy()
                
                # Check rate limiting using existing history
                can_reconnect, limit_msg = self._can_reconnect(pooled_conn)
                if not can_reconnect:
                    logger.warning(f"[SSH Pool] Reconnection rate limit exceeded for {key}")
                    return False, None, limit_msg
                
                # Close the existing connection
                logger.info(f"[SSH Pool] Closing stale connection for reconnection: {key}")
                await pooled_conn.close()
                del self.connections[key]
            else:
                # No existing connection, check if we should create new tracking
                logger.info(f"[SSH Pool] No existing connection to close, creating new one: {key}")
            
            # Create new connection
            try:
                logger.info(f"[SSH Pool] Creating new SSH connection after reconnect: {key}")
                
                if server.auth_type == AuthType.PASSWORD:
                    conn = await asyncssh.connect(
                        host=server.host,
                        port=server.ssh_port,
                        username=server.ssh_user,
                        password=server.ssh_password,
                        known_hosts=None
                    )
                elif server.auth_type == AuthType.KEY_FILE:
                    conn = await asyncssh.connect(
                        host=server.host,
                        port=server.ssh_port,
                        username=server.ssh_user,
                        client_keys=[server.ssh_key_path],
                        known_hosts=None
                    )
                else:
                    return False, None, f"Unsupported auth type: {server.auth_type}"
                
                # Store in pool with preserved reconnection history
                new_pooled_conn = PooledConnection(conn, key)
                new_pooled_conn.reconnection_attempts = reconnection_attempts
                # Record this reconnection attempt AFTER creating the connection
                self._record_reconnection(new_pooled_conn)
                new_pooled_conn.acquire()
                self.connections[key] = new_pooled_conn
                
                logger.info(
                    f"[SSH Pool] ✓ Reconnection successful: {key}. "
                    f"Total connections: {len(self.connections)}"
                )
                
                return True, conn, "Reconnected successfully"
                
            except asyncssh.PermissionDenied:
                logger.error(f"[SSH Pool] ✗ Reconnection failed: Authentication failed for {key}")
                return False, None, "Authentication failed"
            except asyncssh.Error as e:
                logger.error(f"[SSH Pool] ✗ Reconnection failed: SSH error for {key}: {str(e)}")
                return False, None, f"SSH error: {str(e)}"
            except Exception as e:
                logger.error(f"[SSH Pool] ✗ Reconnection failed: Connection error for {key}: {str(e)}")
                return False, None, f"Connection error: {str(e)}"
    
    async def manual_reconnect(self, server: Server) -> Tuple[bool, Optional[asyncssh.SSHClientConnection], str]:
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
        
        async with self.pool_lock:
            # Check if we have an existing connection
            pooled_conn = self.connections.get(key)
            
            if pooled_conn:
                # Close the existing connection
                logger.info(f"[SSH Pool] Manual reconnection: Closing existing connection for {key}")
                await pooled_conn.close()
                del self.connections[key]
            else:
                logger.info(f"[SSH Pool] Manual reconnection: No existing connection to close for {key}")
            
            # Create new connection
            try:
                logger.info(f"[SSH Pool] Manual reconnection: Creating new SSH connection: {key}")
                
                if server.auth_type == AuthType.PASSWORD:
                    conn = await asyncssh.connect(
                        host=server.host,
                        port=server.ssh_port,
                        username=server.ssh_user,
                        password=server.ssh_password,
                        known_hosts=None
                    )
                elif server.auth_type == AuthType.KEY_FILE:
                    conn = await asyncssh.connect(
                        host=server.host,
                        port=server.ssh_port,
                        username=server.ssh_user,
                        client_keys=[server.ssh_key_path],
                        known_hosts=None
                    )
                else:
                    return False, None, f"Unsupported auth type: {server.auth_type}"
                
                # Store in pool with EMPTY reconnection history (reset counter)
                new_pooled_conn = PooledConnection(conn, key)
                new_pooled_conn.reconnection_attempts = []  # Reset to zero
                new_pooled_conn.acquire()
                self.connections[key] = new_pooled_conn
                
                logger.info(
                    f"[SSH Pool] ✓ Manual reconnection successful: {key}. "
                    f"Reconnection counter reset to 0. "
                    f"Total connections: {len(self.connections)}"
                )
                
                return True, conn, "手动重连成功，计数已重置 | Manual reconnection successful, counter reset"
                
            except asyncssh.PermissionDenied:
                logger.error(f"[SSH Pool] ✗ Manual reconnection failed: Authentication failed for {key}")
                return False, None, "认证失败 | Authentication failed"
            except asyncssh.Error as e:
                logger.error(f"[SSH Pool] ✗ Manual reconnection failed: SSH error for {key}: {str(e)}")
                return False, None, f"SSH错误 | SSH error: {str(e)}"
            except Exception as e:
                logger.error(f"[SSH Pool] ✗ Manual reconnection failed: Connection error for {key}: {str(e)}")
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
                return True, f"重连计数已重置 (从 {old_count} 重置为 0) | Reconnection counter reset (from {old_count} to 0)"
            else:
                logger.info(f"[SSH Pool] No connection found for {key}, nothing to reset")
                return True, "无活动连接，无需重置 | No active connection, nothing to reset"
    
    async def release_connection(self, server: Server):
        """
        Release a connection back to the pool
        
        Args:
            server: Server instance
        """
        key = self._create_connection_key(server)
        
        async with self.pool_lock:
            if key in self.connections:
                pooled_conn = self.connections[key]
                pooled_conn.release()
                logger.debug(f"Released connection: {key}")
    
    async def remove_connection(self, server: Server):
        """
        Remove and close a connection from the pool
        
        Args:
            server: Server instance
        """
        key = self._create_connection_key(server)
        
        async with self.pool_lock:
            if key in self.connections:
                pooled_conn = self.connections.pop(key)
                await pooled_conn.close()
                logger.info(f"Removed connection: {key}")
    
    async def close_all(self):
        """Close all connections in the pool"""
        async with self.pool_lock:
            logger.info(f"Closing all {len(self.connections)} connections")
            
            for key, pooled_conn in self.connections.items():
                await pooled_conn.close()
            
            self.connections.clear()
            logger.info("All connections closed")
    
    async def get_pool_stats(self) -> Dict:
        """Get statistics about the connection pool"""
        async with self.pool_lock:
            total = len(self.connections)
            alive = sum(1 for pc in self.connections.values() if pc.is_alive())
            in_use = sum(1 for pc in self.connections.values() if pc.in_use_count > 0)
            
            return {
                'total_connections': total,
                'alive_connections': alive,
                'in_use_connections': in_use,
                'idle_connections': alive - in_use,
                'idle_timeout': self.idle_timeout,
                'max_lifetime': self.max_lifetime
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
                    'connected': pooled_conn.is_alive(),
                    'created_at': pooled_conn.created_at,
                    'last_used': pooled_conn.last_used,
                    'connection_age': now - pooled_conn.created_at,
                    'idle_time': now - pooled_conn.last_used,
                    'in_use': pooled_conn.in_use_count > 0,
                    'reconnection_count': len(recent_reconnections),
                    'max_reconnections': self.max_reconnections_per_hour,
                    'pooling_enabled': True,
                    'connection_key': str(key)
                }
            else:
                return {
                    'connected': False,
                    'created_at': None,
                    'last_used': None,
                    'connection_age': None,
                    'idle_time': None,
                    'in_use': False,
                    'reconnection_count': 0,
                    'max_reconnections': self.max_reconnections_per_hour,
                    'pooling_enabled': True,
                    'connection_key': str(key)
                }


# Global connection pool instance
ssh_connection_pool = SSHConnectionPool()
