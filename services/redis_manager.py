"""
Redis connection and caching utilities (Async)
"""
import redis.asyncio as aioredis
import json
import time
import logging
from typing import Optional, Any
from modules.config import settings

logger = logging.getLogger(__name__)


class RedisManager:
    """Async Redis connection manager for caching with connection pooling"""
    
    # Cache duration constants
    INITIALIZED_SERVER_CACHE_TTL = 2592000  # 30 days in seconds
    
    def __init__(self):
        # Create Redis client with connection pool settings from config
        # The Redis class manages its own connection pool internally
        self.client = aioredis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
            db=settings.REDIS_DB,
            max_connections=settings.REDIS_POOL_SIZE,
            retry_on_timeout=settings.REDIS_RETRY_ON_TIMEOUT,
            health_check_interval=settings.REDIS_HEALTH_CHECK_INTERVAL,
            socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
            decode_responses=True
        )
    
    async def set(self, key: str, value: Any, expire: int = 300) -> bool:
        """Set a value in Redis with optional expiration"""
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            return await self.client.setex(key, expire, value)
        except Exception as e:
            print(f"Redis set error: {e}")
            return False
    
    async def get(self, key: str) -> Optional[Any]:
        """Get a value from Redis"""
        try:
            value = await self.client.get(key)
            if value:
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    return value
            return None
        except Exception as e:
            print(f"Redis get error: {e}")
            return None
    
    async def delete(self, key: str) -> bool:
        """Delete a key from Redis"""
        try:
            return bool(await self.client.delete(key))
        except Exception as e:
            print(f"Redis delete error: {e}")
            return False
    
    async def set_server_status(self, server_id: int, status: str, expire: int = 60) -> bool:
        """Cache server status"""
        key = f"server:{server_id}:status"
        return await self.set(key, status, expire)
    
    async def get_server_status(self, server_id: int) -> Optional[str]:
        """Get cached server status"""
        key = f"server:{server_id}:status"
        return await self.get(key)
    
    async def clear_server_cache(self, server_id: int) -> bool:
        """Clear all cache for a server"""
        pattern = f"server:{server_id}:*"
        try:
            keys = await self.client.keys(pattern)
            if keys:
                await self.client.delete(*keys)
            return True
        except Exception as e:
            print(f"Redis clear cache error: {e}")
            return False
    
    async def ping(self) -> bool:
        """Check Redis connection"""
        try:
            return await self.client.ping()
        except Exception as e:
            print(f"Redis ping error: {e}")
            return False
    
    async def close(self):
        """Close Redis connection and connection pool"""
        await self.client.aclose()
    
    # Initialized server methods
    async def set_initialized_server(self, user_id: int, server_data: dict, expire: int = None) -> str:
        """
        Store initialized server data for a user with 30-day expiration
        Returns: server_key (unique identifier for this server)
        """
        if expire is None:
            expire = self.INITIALIZED_SERVER_CACHE_TTL
        server_key = f"initialized_server:{user_id}:{int(time.time() * 1000)}"
        success = await self.set(server_key, server_data, expire)
        
        if not success:
            raise Exception("Failed to store server data in Redis")
        
        # Also maintain a list of server keys for this user
        list_key = f"user:{user_id}:initialized_servers"
        try:
            await self.client.rpush(list_key, server_key)
            await self.client.expire(list_key, expire)
        except Exception as e:
            # If list update fails, clean up the server data to maintain consistency
            await self.delete(server_key)
            raise Exception(f"Failed to update server list in Redis: {e}")
        
        return server_key
    
    async def get_initialized_servers(self, user_id: int) -> list:
        """Get all initialized servers for a user"""
        list_key = f"user:{user_id}:initialized_servers"
        try:
            server_keys = await self.client.lrange(list_key, 0, -1)
            servers = []
            
            for server_key in server_keys:
                server_data = await self.get(server_key)
                if server_data:  # Only include if not expired
                    server_data['key'] = server_key  # Add key for later retrieval
                    servers.append(server_data)
            
            return servers
        except Exception as e:
            print(f"Redis get initialized servers error: {e}")
            return []
    
    async def get_initialized_server(self, server_key: str) -> Optional[dict]:
        """Get a specific initialized server by key"""
        return await self.get(server_key)
    
    async def delete_initialized_server(self, user_id: int, server_key: str) -> bool:
        """Delete an initialized server"""
        # Remove from user's list
        list_key = f"user:{user_id}:initialized_servers"
        try:
            await self.client.lrem(list_key, 1, server_key)
        except Exception as e:
            print(f"Redis list remove error: {e}")
        
        # Delete the server data
        return await self.delete(server_key)
    
    # Deployment progress methods
    async def append_deployment_progress(self, server_id: int, msg_type: str, message: str, timestamp: str) -> bool:
        """
        Append deployment progress message to Redis list
        
        Args:
            server_id: Server ID
            msg_type: Message type (status|output|error|complete)
            message: Progress message
            timestamp: ISO format timestamp
        
        Returns:
            bool: Success status
        """
        key = f"deployment_progress:{server_id}"
        try:
            # Store as JSON for structured data
            progress_entry = json.dumps({
                "type": msg_type,
                "message": message,
                "timestamp": timestamp
            })
            await self.client.rpush(key, progress_entry)
            # Set expiration to 2 hours (matches deployment lock TTL)
            await self.client.expire(key, 7200)
            return True
        except Exception as e:
            print(f"Redis append deployment progress error: {e}")
            return False
    
    async def get_deployment_progress(self, server_id: int) -> list:
        """
        Get all accumulated deployment progress messages
        
        Args:
            server_id: Server ID
        
        Returns:
            list: List of progress message dicts
        """
        key = f"deployment_progress:{server_id}"
        try:
            progress_entries = await self.client.lrange(key, 0, -1)
            return [json.loads(entry) for entry in progress_entries]
        except Exception as e:
            print(f"Redis get deployment progress error: {e}")
            return []
    
    async def clear_deployment_progress(self, server_id: int) -> bool:
        """
        Clear deployment progress for a server
        
        Args:
            server_id: Server ID
        
        Returns:
            bool: Success status
        """
        key = f"deployment_progress:{server_id}"
        return await self.delete(key)
    
    # Batch action methods
    async def set_batch_action_status(self, batch_id: str, server_id: int, status: str, message: str = "", expire: int = 3600) -> bool:
        """
        Set status for a server in a batch action
        
        Args:
            batch_id: Unique batch action identifier
            server_id: Server ID
            status: Status (pending, in_progress, success, failed)
            message: Optional status message
            expire: TTL in seconds (default 1 hour)
        
        Returns:
            bool: Success status
        """
        key = f"batch_action:{batch_id}:{server_id}"
        try:
            data = json.dumps({
                "status": status,
                "message": message,
                "timestamp": time.time()
            })
            return await self.client.setex(key, expire, data)
        except Exception as e:
            print(f"Redis set batch action status error: {e}")
            return False
    
    async def get_batch_action_status(self, batch_id: str) -> dict:
        """
        Get status for all servers in a batch action
        
        Uses SCAN instead of KEYS to avoid blocking Redis on large datasets.
        
        Args:
            batch_id: Unique batch action identifier
        
        Returns:
            dict: Dictionary of server_id -> status data
        """
        pattern = f"batch_action:{batch_id}:*"
        try:
            results = {}
            cursor = 0
            while True:
                cursor, keys = await self.client.scan(cursor, match=pattern, count=100)
                for key in keys:
                    server_id = key.split(":")[-1]
                    data = await self.get(key)
                    if data:
                        results[server_id] = data
                if cursor == 0:
                    break
            return results
        except Exception as e:
            print(f"Redis get batch action status error: {e}")
            return {}
    
    # Monitoring log methods - uses Redis list with max 50 entries
    MONITORING_LOG_MAX_ENTRIES = 50
    MONITORING_LOG_TTL = 86400 * 7  # 7 days TTL
    
    async def append_monitoring_log(self, server_id: int, event_type: str, status: str, message: str) -> bool:
        """
        Append monitoring log to Redis list, keeping only the last 50 entries.
        New entries replace old ones when limit is exceeded.
        
        Args:
            server_id: Server ID
            event_type: Event type (status_check, auto_restart, monitoring_start, monitoring_stop, a2s_check)
            status: Status (success, failed, info, warning)
            message: Log message
        
        Returns:
            bool: Success status
        """
        key = f"monitoring_logs:{server_id}:{event_type}"
        try:
            # Create log entry with timestamp
            log_entry = json.dumps({
                "id": int(time.time() * 1000),  # Use timestamp as unique ID
                "server_id": server_id,
                "event_type": event_type,
                "status": status,
                "message": message,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")
            })
            
            # Push to the left (newest first)
            await self.client.lpush(key, log_entry)
            
            # Trim to keep only the last 50 entries
            await self.client.ltrim(key, 0, self.MONITORING_LOG_MAX_ENTRIES - 1)
            
            # Set expiration
            await self.client.expire(key, self.MONITORING_LOG_TTL)
            
            logger.debug(f"Appended monitoring log: server={server_id}, type={event_type}, status={status}")
            return True
        except Exception as e:
            logger.error(f"Redis append monitoring log error: {e}")
            return False
    
    async def get_monitoring_logs(self, server_id: int, event_type: str = None, limit: int = 50) -> list:
        """
        Get monitoring logs from Redis.
        
        Args:
            server_id: Server ID
            event_type: Optional event type filter (status_check, auto_restart, a2s_check, etc.)
            limit: Maximum number of logs to return (default 50)
        
        Returns:
            list: List of log entry dicts, newest first
        """
        try:
            if event_type:
                # Get logs for specific event type
                key = f"monitoring_logs:{server_id}:{event_type}"
                log_entries = await self.client.lrange(key, 0, limit - 1)
                logger.debug(f"Retrieved {len(log_entries)} logs for server={server_id}, type={event_type}")
                return [json.loads(entry) for entry in log_entries]
            else:
                # Get all event types and merge
                event_types = ['status_check', 'auto_restart', 'monitoring_start', 'monitoring_stop', 'a2s_check']
                all_logs = []
                
                for etype in event_types:
                    key = f"monitoring_logs:{server_id}:{etype}"
                    log_entries = await self.client.lrange(key, 0, limit - 1)
                    for entry in log_entries:
                        all_logs.append(json.loads(entry))
                
                # Sort by created_at descending
                all_logs.sort(key=lambda x: x.get('created_at', ''), reverse=True)
                
                logger.debug(f"Retrieved {len(all_logs)} total logs for server={server_id}")
                return all_logs[:limit]
        except Exception as e:
            logger.error(f"Redis get monitoring logs error: {e}")
            return []
    
    async def clear_monitoring_logs(self, server_id: int, event_type: str = None) -> bool:
        """
        Clear monitoring logs for a server.
        
        Args:
            server_id: Server ID
            event_type: Optional event type to clear (if None, clears all)
        
        Returns:
            bool: Success status
        """
        try:
            if event_type:
                key = f"monitoring_logs:{server_id}:{event_type}"
                await self.client.delete(key)
            else:
                # Clear all event types
                event_types = ['status_check', 'auto_restart', 'monitoring_start', 'monitoring_stop', 'a2s_check']
                for etype in event_types:
                    key = f"monitoring_logs:{server_id}:{etype}"
                    await self.client.delete(key)
            logger.debug(f"Cleared monitoring logs for server={server_id}, type={event_type or 'all'}")
            return True
        except Exception as e:
            logger.error(f"Redis clear monitoring logs error: {e}")
            return False


# Global Redis manager instance
redis_manager = RedisManager()
