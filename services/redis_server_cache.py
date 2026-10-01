"""Focused helpers; mutable state remains owned by the original controller."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Optional

from services.bounded_output import truncate_utf8_tail

if TYPE_CHECKING:
    from .redis_manager import RedisManager

logger = logging.getLogger("services.redis_manager")


async def set_initialized_server(
    self: RedisManager, user_id: int, server_data: dict, expire: int | None = None
) -> str:
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
    list_key = self.prefixed_key(f"user:{user_id}:initialized_servers")
    try:
        await self.client.rpush(list_key, server_key)
        await self.client.expire(list_key, expire)
    except Exception as e:
        # If list update fails, clean up the server data to maintain consistency
        await self.delete(server_key)
        raise Exception(f"Failed to update server list in Redis: {e}") from e

    return server_key


async def get_initialized_servers(self: RedisManager, user_id: int) -> list:
    """Get all initialized servers for a user"""
    list_key = self.prefixed_key(f"user:{user_id}:initialized_servers")
    try:
        server_keys = await self.client.lrange(list_key, 0, -1)
        servers = []

        for raw_server_key in server_keys:
            server_key = (
                raw_server_key.decode()
                if isinstance(raw_server_key, bytes)
                else str(raw_server_key)
            )
            server_data = await self.get(server_key)
            if server_data:  # Only include if not expired
                server_data["key"] = server_key  # Add key for later retrieval
                servers.append(server_data)

        return servers
    except Exception as e:
        print(f"Redis get initialized servers error: {e}")
        return []


async def get_initialized_server(self: RedisManager, server_key: str) -> Optional[dict]:
    """Get a specific initialized server by key"""
    return await self.get(server_key)


async def delete_initialized_server(self: RedisManager, user_id: int, server_key: str) -> bool:
    """Delete an initialized server"""
    # Remove from user's list
    list_key = self.prefixed_key(f"user:{user_id}:initialized_servers")
    try:
        await self.client.lrem(list_key, 1, server_key)
    except Exception as e:
        print(f"Redis list remove error: {e}")

    # Delete the server data
    return await self.delete(server_key)


async def append_deployment_progress(
    self: RedisManager, server_id: int, msg_type: str, message: str, timestamp: str
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
    key = self.prefixed_key(f"deployment_progress:{server_id}")
    try:
        message = truncate_utf8_tail(
            message,
            self.MAX_DEPLOYMENT_PROGRESS_MESSAGE_BYTES,
        )
        # Store as JSON for structured data
        progress_entry = json.dumps(
            {"type": msg_type, "message": message, "timestamp": timestamp},
            ensure_ascii=False,
        )
        # One batched round trip appends, bounds retained history, and refreshes the
        # two-hour expiry. Long-running SteamCMD jobs can emit thousands of
        # lines, so TTL alone is not a memory bound while a job is active.
        async with self.client.pipeline(transaction=False) as pipeline:
            pipeline.rpush(key, progress_entry)
            pipeline.ltrim(key, -self.MAX_DEPLOYMENT_PROGRESS_ENTRIES, -1)
            pipeline.expire(key, 7200)
            await pipeline.execute()
        return True
    except Exception as e:
        print(f"Redis append deployment progress error: {e}")
        return False


async def get_deployment_progress(self: RedisManager, server_id: int) -> list:
    """
    Get all accumulated deployment progress messages

    Args:
        server_id: Server ID

    Returns:
        list: List of progress message dicts
    """
    key = self.prefixed_key(f"deployment_progress:{server_id}")
    try:
        progress_entries = await self.client.lrange(
            key,
            -self.MAX_DEPLOYMENT_PROGRESS_ENTRIES,
            -1,
        )
        return [json.loads(entry) for entry in progress_entries]
    except Exception as e:
        print(f"Redis get deployment progress error: {e}")
        return []


async def clear_deployment_progress(self: RedisManager, server_id: int) -> bool:
    """
    Clear deployment progress for a server

    Args:
        server_id: Server ID

    Returns:
        bool: Success status
    """
    key = f"deployment_progress:{server_id}"
    return await self.delete(key)
