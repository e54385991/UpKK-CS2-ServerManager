"""
Redis connection and caching utilities (Async)
"""
import redis.asyncio as aioredis
import json
import time
from typing import Optional, Any
from modules.config import settings


class RedisManager:
    """Async Redis connection manager for caching"""
    
    def __init__(self):
        self.client = aioredis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
            db=settings.REDIS_DB,
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
        """Close Redis connection"""
        await self.client.close()
    
    # Initialized server methods
    async def set_initialized_server(self, user_id: int, server_data: dict, expire: int = 86400) -> str:
        """
        Store initialized server data for a user with 24-hour expiration
        Returns: server_key (unique identifier for this server)
        """
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


# Global Redis manager instance
redis_manager = RedisManager()
