"""
SSH Connection Pool for connection reuse and management
Optimizes SSH operations by sharing connections for the same host
"""
import asyncssh
import asyncio
import time
from typing import Optional, Dict, Tuple
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
                 cleanup_interval: int = 60):  # 1 minute
        """
        Initialize connection pool
        
        Args:
            idle_timeout: Close connections idle for this many seconds
            max_lifetime: Close connections older than this many seconds
            cleanup_interval: Run cleanup every N seconds
        """
        if self._initialized:
            return
        
        self._initialized = True
        self.idle_timeout = idle_timeout
        self.max_lifetime = max_lifetime
        self.cleanup_interval = cleanup_interval
        
        # Connection storage: ConnectionKey -> PooledConnection
        self.connections: Dict[ConnectionKey, PooledConnection] = {}
        self.pool_lock = asyncio.Lock()
        
        # Cleanup task
        self.cleanup_task: Optional[asyncio.Task] = None
        
        logger.info(
            f"SSH Connection Pool initialized: "
            f"idle_timeout={idle_timeout}s, max_lifetime={max_lifetime}s"
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
                
                # Verify connection is still alive
                if pooled_conn.is_alive():
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


# Global connection pool instance
ssh_connection_pool = SSHConnectionPool()
