"""
A2S Cache Service
Periodically queries all servers using A2S protocol and caches results in Redis
"""

import asyncio
import logging
import random
from collections.abc import Callable
from typing import Any, Dict, Optional, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from modules.utils import get_current_time
from services.a2s_query import a2s_service
from services.redis_manager import redis_manager

logger = logging.getLogger(__name__)

A2S_CACHE_SERVICE_KEY = "a2s_cache"
SessionFactory = Callable[[], AsyncSession]


class A2SRedisAdapter(Protocol):
    """Minimal cache contract required by the A2S service."""

    async def set(self, key: str, value: Any, expire: int = 300) -> object: ...

    async def get(self, key: str) -> object | None: ...

    async def mget(self, keys: list[str]) -> list[Any | None]: ...


class A2SCacheService:
    """Background service to query and cache A2S server information"""

    def __init__(
        self,
        max_query_concurrency: int = 16,
        *,
        server_deadline: float = 1.2,
        sweep_deadline: float = 9.5,
        redis_adapter: A2SRedisAdapter | None = None,
        session_factory: SessionFactory | None = None,
    ):
        self.query_interval = 30  # Query every 30 seconds
        self.cache_ttl = 60  # Cache TTL in seconds
        self.steam_version_cache_ttl = 3600  # Cache Steam version for 1 hour
        self.max_query_concurrency = max(1, max_query_concurrency)
        self.server_deadline = max(0.01, server_deadline)
        self.sweep_deadline = max(self.server_deadline, sweep_deadline)
        self._redis_adapter = redis_adapter
        # Resolve the legacy factory lazily. Existing callers can keep
        # monkeypatching ``modules.database.async_session_maker`` after this
        # service has been constructed, while application-owned instances bind
        # their own factory permanently.
        self._session_factory = session_factory
        self.task: Optional[asyncio.Task] = None
        self.steam_version_task: Optional[asyncio.Task] = None
        self.running = False
        self._sweep_lock = asyncio.Lock()

    @property
    def redis_adapter(self) -> A2SRedisAdapter:
        """Return this service's cache adapter or the legacy global one."""
        return self._redis_adapter if self._redis_adapter is not None else redis_manager

    @property
    def session_factory(self) -> SessionFactory:
        """Return this service's database boundary or the legacy global one."""
        if self._session_factory is not None:
            return self._session_factory

        from modules.database import async_session_maker

        return async_session_maker

    async def start(self):
        """Start the background A2S query task"""
        if self.task is None or self.task.done():
            self.running = True
            self.task = asyncio.create_task(self._query_loop())
            logger.info("A2S cache service started")

        # Start Steam version caching
        if self.steam_version_task is None or self.steam_version_task.done():
            self.steam_version_task = asyncio.create_task(self._steam_version_loop())
            logger.info("Steam version cache started")

    async def stop(self):
        """Stop the background A2S query task"""
        self.running = False
        tasks = [task for task in (self.task, self.steam_version_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.task = None
        self.steam_version_task = None
        logger.info("A2S cache and Steam version services stopped")

    async def _query_loop(self):
        """Main query loop"""
        while self.running:
            try:
                await self._query_all_servers()
            except Exception as e:
                logger.error(f"Error in A2S query loop: {e}")

            # Spread periodic work across instances and avoid synchronized bursts.
            await asyncio.sleep(self.query_interval * random.uniform(0.9, 1.1))

    async def _steam_version_loop(self):
        """Steam version cache loop - updates every hour"""
        while self.running:
            try:
                await self._cache_steam_version()
            except Exception as e:
                logger.error(f"Error in Steam version cache loop: {e}")

            # Wait for next interval (1 hour), with bounded jitter.
            await asyncio.sleep(self.steam_version_cache_ttl * random.uniform(0.9, 1.1))

    async def _cache_steam_version(self):
        """Fetch and cache latest CS2 version from Steam API"""
        try:
            from services.steam_api_service import steam_api_service

            success, result = await steam_api_service.check_version("1")

            if success and result and result.get("success"):
                steam_version = result.get("required_version")
                if steam_version:
                    cache_data = {
                        "version": steam_version,
                        "message": result.get("message", ""),
                        "timestamp": get_current_time().isoformat(),
                    }

                    await self.redis_adapter.set(
                        "steam:latest_version", cache_data, expire=self.steam_version_cache_ttl
                    )

                    logger.info(f"Cached latest Steam CS2 version: {steam_version}")
        except Exception as e:
            logger.error(f"Error caching Steam version: {e}")

    async def get_latest_steam_version(self) -> Optional[Dict]:
        """Get cached Steam version info"""
        try:
            cached = await self.redis_adapter.get("steam:latest_version")
            if cached and isinstance(cached, dict):
                return cached
            return None
        except Exception as e:
            logger.error(f"Error getting cached Steam version: {e}")
            return None

    async def _query_all_servers(self):
        """Query all servers and cache results"""
        from sqlmodel import select

        from modules.models import Server

        if self._sweep_lock.locked():
            logger.debug("Skipping overlapping A2S sweep")
            return

        try:
            async with self._sweep_lock, asyncio.timeout(self.sweep_deadline):
                await self._run_server_sweep(select, self.session_factory, Server)
        except TimeoutError:
            logger.warning("A2S sweep exceeded %.2fs deadline", self.sweep_deadline)
        except Exception as e:
            logger.error(f"Error querying servers: {e}")

    async def _run_server_sweep(self, select, async_session_maker, server_model):
        """Run one bounded sweep while the single-flight lock is held."""
        try:
            # Fetch server list quickly and close DB connection to avoid pool exhaustion
            async with async_session_maker() as db:
                result = await db.execute(select(server_model))
                servers = result.scalars().all()

            logger.debug(f"Querying {len(servers)} servers for A2S info")

            # DB session is already closed, so network operations won't hold DB
            # connections. Bound fan-out to protect UDP sockets and thread workers.
            semaphore = asyncio.Semaphore(self.max_query_concurrency)

            async def query_server(server):
                # Skip servers that are marked as down due to SSH failures
                if server.should_skip_background_checks():
                    logger.debug(
                        f"Skipping A2S query for server {server.id} - marked as SSH down for 3+ days"
                    )
                    return

                async with semaphore:
                    try:
                        async with asyncio.timeout(self.server_deadline):
                            await self._query_and_cache_server(server)
                    except TimeoutError:
                        logger.warning(
                            "A2S query for server %s exceeded %.2fs deadline",
                            server.id,
                            self.server_deadline,
                        )
                        await self._cache_query_error(server.id, "A2S query deadline exceeded")

            await asyncio.gather(*(query_server(server) for server in servers))

        except Exception as e:
            logger.error(f"Error querying servers: {e}")

    async def _cache_query_error(self, server_id: int, error: str) -> None:
        cache_data = {
            "success": False,
            "error": error,
            "timestamp": get_current_time().isoformat(),
        }
        try:
            await self.redis_adapter.set(
                f"a2s:server:{server_id}",
                cache_data,
                expire=self.cache_ttl,
            )
        except Exception:
            logger.debug("Unable to cache A2S error for server %s", server_id, exc_info=True)

    async def _query_and_cache_server(self, server):
        """Query a single server and cache the result"""
        try:
            loop = asyncio.get_running_loop()
            start_time = loop.time()

            # Use configured A2S host/port or fall back to server host/game_port
            query_host = server.a2s_query_host or server.host
            query_port = server.a2s_query_port or server.game_port

            # Query server info
            info_success, server_info = await a2s_service.query_server_info(
                query_host, query_port, timeout=3.0
            )

            # Calculate response time
            response_time = int((loop.time() - start_time) * 1000)

            # Query players if server info was successful
            players_success = False
            player_list = None
            if info_success:
                players_success, player_list = await a2s_service.query_players(
                    query_host, query_port, timeout=3.0
                )

            # Build cache data
            cache_data = {
                "query_host": query_host,
                "query_port": query_port,
                "success": info_success,
                "server_info": server_info,
                "players": player_list if players_success else [],
                "response_time_ms": response_time,
                "timestamp": get_current_time().isoformat(),
                "last_updated": get_current_time().isoformat(),
            }

            # Store in Redis with TTL
            cache_key = f"a2s:server:{server.id}"
            await self.redis_adapter.set(cache_key, cache_data, expire=self.cache_ttl)

            # Update server's current_game_version in database if we got version from A2S
            if info_success and server_info and server_info.get("version"):
                from services.steam_api_service import steam_api_service

                parsed_version = steam_api_service.parse_version_from_a2s(
                    server_info.get("version")
                )
                if parsed_version and parsed_version != server.current_game_version:
                    # Update the server's version in the database
                    try:
                        async with self.session_factory() as db:
                            from sqlmodel import update

                            from modules.models import Server

                            await db.execute(
                                update(Server)
                                .where(Server.id == server.id)
                                .values(current_game_version=parsed_version)
                            )
                            await db.commit()
                            logger.info(f"Updated server {server.id} version to {parsed_version}")
                    except Exception as e:
                        logger.error(f"Failed to update server version in DB: {e}")

            if info_success and server_info:
                logger.debug(
                    f"Cached A2S info for server {server.id} ({server.name}): "
                    f"{server_info.get('server_name', 'N/A')} - "
                    f"{server_info.get('player_count', 0)}/{server_info.get('max_players', 0)} players"
                )
            else:
                logger.debug(f"Server {server.id} ({server.name}) A2S query failed")

        except Exception as e:
            logger.error(f"Error querying server {server.id}: {e}")
            await self._cache_query_error(server.id, str(e))

    async def get_cached_info(self, server_id: int) -> Optional[Dict]:
        """Get cached A2S info for a server"""
        cache_key = f"a2s:server:{server_id}"
        try:
            cached = await self.redis_adapter.get(cache_key)
            return self._normalize_cached_info(server_id, cached)
        except Exception as e:
            logger.error(f"Error getting cached A2S info for server {server_id}: {e}")
            return None

    async def get_cached_info_many(self, server_ids: list[int]) -> Dict[int, Optional[Dict]]:
        """Fetch cached A2S data for many servers with one Redis MGET."""
        unique_server_ids = list(dict.fromkeys(server_ids))
        keys = [f"a2s:server:{server_id}" for server_id in unique_server_ids]
        cached_values = await self.redis_adapter.mget(keys)
        return {
            server_id: self._normalize_cached_info(server_id, cached)
            for server_id, cached in zip(unique_server_ids, cached_values, strict=True)
        }

    @staticmethod
    def _normalize_cached_info(server_id: int, cached: object) -> Optional[Dict]:
        """Normalize current cache values and values from the old double-JSON bug."""
        if isinstance(cached, dict):
            return cached
        if isinstance(cached, str):
            import json

            try:
                parsed = json.loads(cached)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError, TypeError:
                pass
        if cached is not None:
            logger.warning("Invalid cached data type for server %s: %s", server_id, type(cached))
        return None


# Global instance
a2s_cache_service = A2SCacheService()
