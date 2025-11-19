"""
Redis connection and caching utilities (Async)
"""
import redis.asyncio as aioredis
import json
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


# Global Redis manager instance
redis_manager = RedisManager()
