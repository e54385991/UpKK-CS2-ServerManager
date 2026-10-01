"""Focused helpers; mutable state remains owned by the original controller."""

from __future__ import annotations

import json
import logging
import time
import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .redis_manager import RedisManager

logger = logging.getLogger("services.redis_manager")


async def set_batch_action_status(
    self: RedisManager,
    batch_id: str,
    server_id: int,
    status: str,
    message: str = "",
    expire: int = 3600,
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
    key = self.prefixed_key(f"batch_action:{batch_id}:{server_id}")
    try:
        data = json.dumps({"status": status, "message": message, "timestamp": time.time()})
        return await self._set_with_expiry(key, data, expire)
    except Exception as e:
        print(f"Redis set batch action status error: {e}")
        return False


async def set_batch_action_statuses(
    self: RedisManager,
    batch_id: str,
    server_ids: list[int],
    status: str,
    message: str = "",
    expire: int = 3600,
) -> bool:
    """Initialize a batch in one non-transactional Redis pipeline."""
    timestamp = time.time()
    try:
        async with self.client.pipeline(transaction=False) as pipeline:
            for server_id in server_ids:
                key = self.prefixed_key(f"batch_action:{batch_id}:{server_id}")
                data = json.dumps({"status": status, "message": message, "timestamp": timestamp})
                setter = getattr(pipeline, "set", None)
                if setter is not None:
                    setter(key, data, ex=expire)
                else:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", DeprecationWarning)
                        pipeline.setex(key, expire, data)
            await pipeline.execute()
        return True
    except Exception as e:
        print(f"Redis set batch action statuses error: {e}")
        return False


async def get_batch_action_status(self: RedisManager, batch_id: str) -> dict:
    """
    Get status for all servers in a batch action

    Uses SCAN instead of KEYS to avoid blocking Redis on large datasets.

    Args:
        batch_id: Unique batch action identifier

    Returns:
        dict: Dictionary of server_id -> status data
    """
    pattern = self.prefixed_key(f"batch_action:{batch_id}:*")
    try:
        results = {}
        cursor = 0
        while True:
            cursor, keys = await self.client.scan(cursor, match=pattern, count=100)
            values = await self.client.mget(keys) if keys else []
            for key, value in zip(keys, values, strict=True):
                if not value:
                    continue
                normalized_key = key.decode() if isinstance(key, bytes) else key
                server_id = normalized_key.rsplit(":", 1)[-1]
                try:
                    results[server_id] = json.loads(value)
                except TypeError, json.JSONDecodeError:
                    results[server_id] = value
            if cursor == 0:
                break
        return results
    except Exception as e:
        print(f"Redis get batch action status error: {e}")
        return {}


async def set_batch_action_meta(
    self: RedisManager, batch_id: str, *, actor_user_id: int, action: str, expire: int = 3600
) -> bool:
    """Store the actor and action for a batch journal (separate from per-server keys)."""
    key = self.prefixed_key(f"batch_meta:{batch_id}")
    try:
        data = json.dumps(
            {"actor_user_id": actor_user_id, "action": action, "timestamp": time.time()}
        )
        return await self._set_with_expiry(key, data, expire)
    except Exception as e:
        print(f"Redis set batch action meta error: {e}")
        return False


async def get_batch_action_meta(self: RedisManager, batch_id: str) -> dict | None:
    """Return actor metadata for a batch, or None when expired/missing."""
    key = self.prefixed_key(f"batch_meta:{batch_id}")
    try:
        value = await self.client.get(key)
        if not value:
            return None
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except TypeError, json.JSONDecodeError:
        return None
    except Exception as e:
        print(f"Redis get batch action meta error: {e}")
        return None


async def append_monitoring_log(
    self: RedisManager, server_id: int, event_type: str, status: str, message: str
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
    key = self.prefixed_key(f"monitoring_logs:{server_id}:{event_type}")
    try:
        # Create log entry with timestamp
        log_entry = json.dumps(
            {
                "id": int(time.time() * 1000),  # Use timestamp as unique ID
                "server_id": server_id,
                "event_type": event_type,
                "status": status,
                "message": message,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )

        # Push to the left (newest first)
        await self.client.lpush(key, log_entry)

        # Trim to keep only the last 50 entries
        await self.client.ltrim(key, 0, self.MONITORING_LOG_MAX_ENTRIES - 1)

        # Set expiration
        await self.client.expire(key, self.MONITORING_LOG_TTL)

        logger.debug(
            f"Appended monitoring log: server={server_id}, type={event_type}, status={status}"
        )
        return True
    except Exception as e:
        logger.error(f"Redis append monitoring log error: {e}")
        return False


async def get_monitoring_logs(
    self: RedisManager, server_id: int, event_type: str | None = None, limit: int = 50
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
    try:
        if event_type:
            # Get logs for specific event type
            key = self.prefixed_key(f"monitoring_logs:{server_id}:{event_type}")
            log_entries = await self.client.lrange(key, 0, limit - 1)
            logger.debug(
                f"Retrieved {len(log_entries)} logs for server={server_id}, type={event_type}"
            )
            return [json.loads(entry) for entry in log_entries]
        else:
            # Get all event types and merge
            event_types = [
                "status_check",
                "auto_restart",
                "monitoring_start",
                "monitoring_stop",
                "a2s_check",
            ]
            all_logs = []

            for etype in event_types:
                key = self.prefixed_key(f"monitoring_logs:{server_id}:{etype}")
                log_entries = await self.client.lrange(key, 0, limit - 1)
                for entry in log_entries:
                    all_logs.append(json.loads(entry))

            # Sort by created_at descending
            all_logs.sort(key=lambda x: x.get("created_at", ""), reverse=True)

            logger.debug(f"Retrieved {len(all_logs)} total logs for server={server_id}")
            return all_logs[:limit]
    except Exception as e:
        logger.error(f"Redis get monitoring logs error: {e}")
        return []


async def clear_monitoring_logs(
    self: RedisManager, server_id: int, event_type: str | None = None
) -> bool:
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
            key = self.prefixed_key(f"monitoring_logs:{server_id}:{event_type}")
            await self.client.delete(key)
        else:
            # Clear all event types
            event_types = [
                "status_check",
                "auto_restart",
                "monitoring_start",
                "monitoring_stop",
                "a2s_check",
            ]
            for etype in event_types:
                key = self.prefixed_key(f"monitoring_logs:{server_id}:{etype}")
                await self.client.delete(key)
        logger.debug(f"Cleared monitoring logs for server={server_id}, type={event_type or 'all'}")
        return True
    except Exception as e:
        logger.error(f"Redis clear monitoring logs error: {e}")
        return False
