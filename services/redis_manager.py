"""
Redis connection and caching utilities (Async)
"""

import asyncio
import json
import logging
import time
import warnings
from collections.abc import Sequence
from typing import Any, Optional

import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from modules.config import settings
from modules.observability import is_monitor_io, record_redis
from services.bounded_output import truncate_utf8_tail as truncate_utf8_tail

from .redis_event_cache import append_monitoring_log as _delegated_append_monitoring_log
from .redis_event_cache import clear_monitoring_logs as _delegated_clear_monitoring_logs
from .redis_event_cache import get_batch_action_meta as _delegated_get_batch_action_meta
from .redis_event_cache import get_batch_action_status as _delegated_get_batch_action_status
from .redis_event_cache import get_monitoring_logs as _delegated_get_monitoring_logs
from .redis_event_cache import set_batch_action_meta as _delegated_set_batch_action_meta
from .redis_event_cache import set_batch_action_status as _delegated_set_batch_action_status
from .redis_event_cache import set_batch_action_statuses as _delegated_set_batch_action_statuses
from .redis_server_cache import append_deployment_progress as _delegated_append_deployment_progress
from .redis_server_cache import clear_deployment_progress as _delegated_clear_deployment_progress
from .redis_server_cache import delete_initialized_server as _delegated_delete_initialized_server
from .redis_server_cache import get_deployment_progress as _delegated_get_deployment_progress
from .redis_server_cache import get_initialized_server as _delegated_get_initialized_server
from .redis_server_cache import get_initialized_servers as _delegated_get_initialized_servers
from .redis_server_cache import set_initialized_server as _delegated_set_initialized_server

logger = logging.getLogger(__name__)


def _observe_redis(
    *, duration_ms: float | None = None, failed: bool = False, timeout: bool = False
) -> None:
    if is_monitor_io():
        record_redis(monitor=True)
        return
    record_redis(duration_ms=duration_ms, failed=failed, timeout=timeout)


class RedisManager:
    """Async Redis connection manager for caching with connection pooling"""

    # Cache duration constants
    INITIALIZED_SERVER_CACHE_TTL = 2592000  # 30 days in seconds

    def __init__(self):
        self._coordination_retry_after = 0.0
        # Create Redis client with connection pool settings from config
        # The Redis class manages its own connection pool internally
        self.client = aioredis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
            db=settings.REDIS_DB,
            max_connections=settings.REDIS_POOL_SIZE,
            health_check_interval=settings.REDIS_HEALTH_CHECK_INTERVAL,
            socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
            # redis-py's default retries a failed connect/command 10 times with
            # backoff, so an outage stalls every cache read for seconds (tens of
            # seconds when the host is unreachable). Callers already fall back to
            # a cache miss; one immediate retry still replaces a stale pooled socket.
            retry=Retry(NoBackoff(), 1),
            decode_responses=True,
        )

    def prefixed_key(self, key: str) -> str:
        """Namespace a Redis key so two panels can share one Redis DB."""
        prefix = (settings.REDIS_KEY_PREFIX or "").strip()
        if not prefix:
            return key
        if not prefix.endswith(":"):
            prefix = f"{prefix}:"
        if key.startswith(prefix):
            return key
        return f"{prefix}{key}"

    async def _set_with_expiry(self, key: str, value: Any, expire: int) -> bool:
        """Use the current Redis API while keeping lightweight test clients compatible."""
        setex = getattr(self.client, "setex", None)
        if setex is not None and getattr(setex, "__self__", None) is None:
            return bool(await setex(key, expire, value))
        try:
            result = await self.client.set(key, value, ex=expire)
            if result:
                return True
        except AttributeError, TypeError, RuntimeError:
            pass
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            return bool(await self.client.setex(key, expire, value))

    async def set(self, key: str, value: Any, expire: int = 300) -> bool:
        """Set a value in Redis with optional expiration"""
        key = self.prefixed_key(key)
        started = time.monotonic()
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            result = await self._set_with_expiry(key, value, expire)
            _observe_redis(duration_ms=(time.monotonic() - started) * 1000)
            return result
        except TimeoutError:
            _observe_redis(timeout=True, failed=True)
            return False
        except Exception as e:
            _observe_redis(failed=True)
            print(f"Redis set error: {e}")
            return False

    @staticmethod
    def _decode_value(value: Any) -> Any | None:
        """Preserve the single-key JSON/string/missing-value contract."""
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    async def get(self, key: str) -> Optional[Any]:
        """Get a value from Redis."""
        try:
            started = time.monotonic()
            value = await self.client.get(self.prefixed_key(key))
            _observe_redis(duration_ms=(time.monotonic() - started) * 1000)
            return self._decode_value(value)
        except Exception as e:
            _observe_redis(failed=True)
            print(f"Redis get error: {e}")
            return None

    async def get_many(self, keys: Sequence[str]) -> list[Any | None]:
        """Read ordered cache values in one MGET, including duplicate and missing keys."""
        if not keys:
            return []
        try:
            started = time.monotonic()
            values = await self.client.mget([self.prefixed_key(key) for key in keys])
            _observe_redis(duration_ms=(time.monotonic() - started) * 1000)
            return [self._decode_value(value) for value in values]
        except Exception:
            _observe_redis(failed=True)
            logger.warning("Redis batch cache read failed (keys=%d)", len(keys))
            return [None] * len(keys)

    async def getdel(self, key: str) -> Optional[Any]:
        """Atomically get and delete a namespaced key (Redis GETDEL)."""
        prefixed = self.prefixed_key(key)
        started = time.monotonic()
        try:
            try:
                value = await self.client.getdel(prefixed)
            except AttributeError:
                value = await self.client.get(prefixed)
                if value:
                    await self.client.delete(prefixed)
            _observe_redis(duration_ms=(time.monotonic() - started) * 1000)
            return self._decode_value(value)
        except TimeoutError:
            _observe_redis(timeout=True, failed=True)
            return None
        except Exception as e:
            _observe_redis(failed=True)
            print(f"Redis getdel error: {e}")
            return None

    async def delete(self, key: str) -> bool:
        """Delete a key from Redis"""
        key = self.prefixed_key(key)
        started = time.monotonic()
        try:
            result = bool(await self.client.delete(key))
            _observe_redis(duration_ms=(time.monotonic() - started) * 1000)
            return result
        except TimeoutError:
            _observe_redis(timeout=True, failed=True)
            return False
        except Exception as e:
            _observe_redis(failed=True)
            print(f"Redis delete error: {e}")
            return False

    async def acquire_lock(self, key: str, token: str, expire: int) -> Optional[bool]:
        """Atomically acquire a distributed lock; return None when Redis is unavailable."""
        key = self.prefixed_key(key)
        if time.monotonic() < self._coordination_retry_after:
            return None
        try:
            result = await asyncio.wait_for(
                self.client.set(key, token, nx=True, ex=expire),
                timeout=0.75,
            )
            return bool(result)
        except Exception as e:
            self._coordination_retry_after = time.monotonic() + 10
            logger.error("Redis lock acquire failed for %s: %s", key, e)
            return None

    async def is_lock_held(self, key: str) -> Optional[bool]:
        """Check a coordination lock with the same short deadline/circuit breaker."""
        key = self.prefixed_key(key)
        if time.monotonic() < self._coordination_retry_after:
            return None
        try:
            return bool(await asyncio.wait_for(self.client.exists(key), timeout=0.75))
        except Exception as e:
            self._coordination_retry_after = time.monotonic() + 10
            logger.error("Redis lock check failed for %s: %s", key, e)
            return None

    async def get_lock_token(self, key: str) -> Optional[str]:
        """Read a lock token for guarded stale-lock reconciliation."""
        key = self.prefixed_key(key)
        if time.monotonic() < self._coordination_retry_after:
            return None
        try:
            value = await asyncio.wait_for(self.client.get(key), timeout=0.75)
            return str(value) if value else None
        except Exception as e:
            self._coordination_retry_after = time.monotonic() + 10
            logger.error("Redis lock token read failed for %s: %s", key, e)
            return None

    async def refresh_lock(self, key: str, token: str, expire: int) -> bool:
        """Extend a lock only when it is still owned by ``token``."""
        key = self.prefixed_key(key)
        if time.monotonic() < self._coordination_retry_after:
            return False
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('expire', KEYS[1], ARGV[2])
        end
        return 0
        """
        try:
            return bool(
                await asyncio.wait_for(
                    self.client.eval(script, 1, key, token, expire),
                    timeout=0.75,
                )
            )
        except Exception as e:
            self._coordination_retry_after = time.monotonic() + 10
            logger.error("Redis lock refresh failed for %s: %s", key, e)
            return False

    async def release_lock(self, key: str, token: str) -> bool:
        """Release a distributed lock without deleting a newer owner's lock."""
        key = self.prefixed_key(key)
        if time.monotonic() < self._coordination_retry_after:
            return False
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        end
        return 0
        """
        try:
            return bool(
                await asyncio.wait_for(
                    self.client.eval(script, 1, key, token),
                    timeout=0.75,
                )
            )
        except Exception as e:
            self._coordination_retry_after = time.monotonic() + 10
            logger.error("Redis lock release failed for %s: %s", key, e)
            return False

    async def hit_rate_limit(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        """Increment a fixed-window counter atomically and return (allowed, retry_after)."""
        key = self.prefixed_key(key)
        if time.monotonic() < self._coordination_retry_after:
            return True, 0
        script = """
        local count = redis.call('incr', KEYS[1])
        if count == 1 then redis.call('expire', KEYS[1], ARGV[2]) end
        local ttl = redis.call('ttl', KEYS[1])
        return {count, ttl}
        """
        try:
            count, ttl = await asyncio.wait_for(
                self.client.eval(script, 1, key, limit, window),
                timeout=0.75,
            )
            return int(count) <= limit, max(1, int(ttl))
        except Exception as e:
            self._coordination_retry_after = time.monotonic() + 10
            # Authentication must remain available during a cache outage; fail open and log.
            logger.error("Redis rate limit failed for %s: %s", key, e)
            return True, 0

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
        pattern = self.prefixed_key(f"server:{server_id}:*")
        try:
            await self.delete_by_pattern(pattern)
            return True
        except Exception as e:
            print(f"Redis clear cache error: {e}")
            return False

    async def delete_by_pattern(self, pattern: str, count: int = 100) -> int:
        """Delete keys matching a pattern without blocking Redis."""
        pattern = self.prefixed_key(pattern)
        deleted = 0
        cursor = 0
        while True:
            cursor, keys = await self.client.scan(cursor, match=pattern, count=count)
            if keys:
                deleted += await self.client.delete(*keys)
            if cursor == 0:
                break
        return deleted

    async def ping(self) -> bool:
        """Check Redis connection"""
        started = time.monotonic()
        try:
            result = await self.client.ping()
            _observe_redis(duration_ms=(time.monotonic() - started) * 1000, failed=not result)
            return bool(result)
        except TimeoutError:
            _observe_redis(timeout=True, failed=True)
            return False
        except Exception as e:
            _observe_redis(failed=True)
            print(f"Redis ping error: {e}")
            return False

    async def close(self):
        """Close Redis connection and connection pool"""
        await self.client.aclose()

    # Initialized server methods
    async def set_initialized_server(
        self, user_id: int, server_data: dict, expire: int | None = None
    ) -> str:
        """
        Store initialized server data for a user with 30-day expiration
        Returns: server_key (unique identifier for this server)
        """
        return await _delegated_set_initialized_server(self, user_id, server_data, expire)

    async def get_initialized_servers(self, user_id: int) -> list:
        """Get all initialized servers for a user"""
        return await _delegated_get_initialized_servers(self, user_id)

    async def get_initialized_server(self, server_key: str) -> Optional[dict]:
        """Get a specific initialized server by key"""
        return await _delegated_get_initialized_server(self, server_key)

    async def delete_initialized_server(self, user_id: int, server_key: str) -> bool:
        """Delete an initialized server"""
        return await _delegated_delete_initialized_server(self, user_id, server_key)

    # Deployment progress methods
    MAX_DEPLOYMENT_PROGRESS_ENTRIES = 300
    MAX_DEPLOYMENT_PROGRESS_MESSAGE_BYTES = 64 * 1024

    async def append_deployment_progress(
        self, server_id: int, msg_type: str, message: str, timestamp: str
    ) -> bool:
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
        return await _delegated_append_deployment_progress(
            self, server_id, msg_type, message, timestamp
        )

    async def get_deployment_progress(self, server_id: int) -> list:
        """
        Get all accumulated deployment progress messages

        Args:
            server_id: Server ID

        Returns:
            list: List of progress message dicts
        """
        return await _delegated_get_deployment_progress(self, server_id)

    async def clear_deployment_progress(self, server_id: int) -> bool:
        """
        Clear deployment progress for a server

        Args:
            server_id: Server ID

        Returns:
            bool: Success status
        """
        return await _delegated_clear_deployment_progress(self, server_id)

    # Batch action methods
    async def set_batch_action_status(
        self, batch_id: str, server_id: int, status: str, message: str = "", expire: int = 3600
    ) -> bool:
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
        return await _delegated_set_batch_action_status(
            self, batch_id, server_id, status, message, expire
        )

    async def set_batch_action_statuses(
        self,
        batch_id: str,
        server_ids: list[int],
        status: str,
        message: str = "",
        expire: int = 3600,
    ) -> bool:
        """Initialize a batch in one non-transactional Redis pipeline."""
        return await _delegated_set_batch_action_statuses(
            self, batch_id, server_ids, status, message, expire
        )

    async def get_batch_action_status(self, batch_id: str) -> dict:
        """
        Get status for all servers in a batch action

        Uses SCAN instead of KEYS to avoid blocking Redis on large datasets.

        Args:
            batch_id: Unique batch action identifier

        Returns:
            dict: Dictionary of server_id -> status data
        """
        return await _delegated_get_batch_action_status(self, batch_id)

    async def set_batch_action_meta(
        self,
        batch_id: str,
        *,
        actor_user_id: int,
        action: str,
        expire: int = 3600,
    ) -> bool:
        """Store the actor and action for a batch journal (separate from per-server keys)."""
        return await _delegated_set_batch_action_meta(
            self, batch_id, actor_user_id=actor_user_id, action=action, expire=expire
        )

    async def get_batch_action_meta(self, batch_id: str) -> dict | None:
        """Return actor metadata for a batch, or None when expired/missing."""
        return await _delegated_get_batch_action_meta(self, batch_id)

    # Monitoring log methods - uses Redis list with max 50 entries
    MONITORING_LOG_MAX_ENTRIES = 50
    MONITORING_LOG_TTL = 86400 * 7  # 7 days TTL

    async def append_monitoring_log(
        self, server_id: int, event_type: str, status: str, message: str
    ) -> bool:
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
        return await _delegated_append_monitoring_log(self, server_id, event_type, status, message)

    async def get_monitoring_logs(
        self, server_id: int, event_type: str | None = None, limit: int = 50
    ) -> list:
        """
        Get monitoring logs from Redis.

        Args:
            server_id: Server ID
            event_type: Optional event type filter (status_check, auto_restart, a2s_check, etc.)
            limit: Maximum number of logs to return (default 50)

        Returns:
            list: List of log entry dicts, newest first
        """
        return await _delegated_get_monitoring_logs(self, server_id, event_type, limit)

    async def clear_monitoring_logs(self, server_id: int, event_type: str | None = None) -> bool:
        """
        Clear monitoring logs for a server.

        Args:
            server_id: Server ID
            event_type: Optional event type to clear (if None, clears all)

        Returns:
            bool: Success status
        """
        return await _delegated_clear_monitoring_logs(self, server_id, event_type)


# Global Redis manager instance
redis_manager = RedisManager()
