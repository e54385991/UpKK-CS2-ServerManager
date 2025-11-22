"""
A2S Cache Service
Periodically queries all servers using A2S protocol and caches results in Redis
"""
import asyncio
import logging
from typing import Dict, Optional

from services.a2s_query import a2s_service
from services.redis_manager import redis_manager
from modules.utils import get_current_time

logger = logging.getLogger(__name__)


class A2SCacheService:
    """Background service to query and cache A2S server information"""
    
    def __init__(self):
        self.query_interval = 30  # Query every 30 seconds
        self.cache_ttl = 60  # Cache TTL in seconds
        self.steam_version_cache_ttl = 3600  # Cache Steam version for 1 hour
        self.task: Optional[asyncio.Task] = None
        self.steam_version_task: Optional[asyncio.Task] = None
        self.running = False
        
    async def start(self):
        """Start the background A2S query task"""
        if self.task is None or self.task.done():
            self.running = True
            self.task = asyncio.create_task(self._query_loop())
            logger.info("A2S cache service started")
            # Do an immediate first query
            await self._query_all_servers()
            
        # Start Steam version caching
        if self.steam_version_task is None or self.steam_version_task.done():
            self.steam_version_task = asyncio.create_task(self._steam_version_loop())
            logger.info("Steam version cache started")
            # Do an immediate first fetch
            await self._cache_steam_version()
    
    def stop(self):
        """Stop the background A2S query task"""
        self.running = False
        if self.task and not self.task.done():
            self.task.cancel()
            logger.info("A2S cache service stopped")
        if self.steam_version_task and not self.steam_version_task.done():
            self.steam_version_task.cancel()
            logger.info("Steam version cache stopped")
    
    async def _query_loop(self):
        """Main query loop"""
        while self.running:
            try:
                await self._query_all_servers()
            except Exception as e:
                logger.error(f"Error in A2S query loop: {e}")
            
            # Wait for next interval
            await asyncio.sleep(self.query_interval)
    
    async def _steam_version_loop(self):
        """Steam version cache loop - updates every hour"""
        while self.running:
            try:
                await self._cache_steam_version()
            except Exception as e:
                logger.error(f"Error in Steam version cache loop: {e}")
            
            # Wait for next interval (1 hour)
            await asyncio.sleep(self.steam_version_cache_ttl)
    
    async def _cache_steam_version(self):
        """Fetch and cache latest CS2 version from Steam API"""
        try:
            from services.steam_api_service import steam_api_service
            
            success, result = await steam_api_service.check_version("1")
            
            if success and result.get('success'):
                steam_version = result.get('required_version')
                if steam_version:
                    cache_data = {
                        'version': steam_version,
                        'message': result.get('message', ''),
                        'timestamp': get_current_time().isoformat()
                    }
                    
                    await redis_manager.set(
                        'steam:latest_version',
                        cache_data,
                        expire=self.steam_version_cache_ttl
                    )
                    
                    logger.info(f"Cached latest Steam CS2 version: {steam_version}")
        except Exception as e:
            logger.error(f"Error caching Steam version: {e}")
    
    async def get_latest_steam_version(self) -> Optional[Dict]:
        """Get cached Steam version info"""
        try:
            cached = await redis_manager.get('steam:latest_version')
            if cached and isinstance(cached, dict):
                return cached
            return None
        except Exception as e:
            logger.error(f"Error getting cached Steam version: {e}")
            return None
    
    async def _query_all_servers(self):
        """Query all servers and cache results"""
        from modules.database import async_session_maker
        from modules.models import Server
        from sqlalchemy import select
        
        try:
            async with async_session_maker() as db:
                # Get all servers
                result = await db.execute(select(Server))
                servers = result.scalars().all()
                
                logger.debug(f"Querying {len(servers)} servers for A2S info")
                
                # Query each server
                for server in servers:
                    await self._query_and_cache_server(server)
                    
        except Exception as e:
            logger.error(f"Error querying servers: {e}")
    
    async def _query_and_cache_server(self, server):
        """Query a single server and cache the result"""
        try:
            start_time = asyncio.get_event_loop().time()
            
            # Use configured A2S host/port or fall back to server host/game_port
            query_host = server.a2s_query_host or server.host
            query_port = server.a2s_query_port or server.game_port
            
            # Query server info
            info_success, server_info = await a2s_service.query_server_info(
                query_host, query_port, timeout=3.0
            )
            
            # Calculate response time
            response_time = int((asyncio.get_event_loop().time() - start_time) * 1000)
            
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
                "last_updated": get_current_time().isoformat()
            }
            
            # Store in Redis with TTL
            cache_key = f"a2s:server:{server.id}"
            await redis_manager.set(
                cache_key,
                cache_data,
                expire=self.cache_ttl
            )
            
            # Update server's current_game_version in database if we got version from A2S
            if info_success and server_info and server_info.get('version'):
                from services.steam_api_service import steam_api_service
                parsed_version = steam_api_service.parse_version_from_a2s(server_info.get('version'))
                if parsed_version and parsed_version != server.current_game_version:
                    # Update the server's version in the database
                    from modules.database import async_session_maker
                    try:
                        async with async_session_maker() as db:
                            from sqlalchemy import select, update
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
            
            if info_success:
                logger.debug(
                    f"Cached A2S info for server {server.id} ({server.name}): "
                    f"{server_info.get('server_name', 'N/A')} - "
                    f"{server_info.get('player_count', 0)}/{server_info.get('max_players', 0)} players"
                )
            else:
                logger.debug(f"Server {server.id} ({server.name}) A2S query failed")
                
        except Exception as e:
            logger.error(f"Error querying server {server.id}: {e}")
            # Cache the error state
            cache_data = {
                "success": False,
                "error": str(e),
                "timestamp": get_current_time().isoformat()
            }
            cache_key = f"a2s:server:{server.id}"
            try:
                await redis_manager.set(
                    cache_key,
                    cache_data,
                    expire=self.cache_ttl
                )
            except Exception:
                pass
    
    async def get_cached_info(self, server_id: int) -> Optional[Dict]:
        """Get cached A2S info for a server"""
        cache_key = f"a2s:server:{server_id}"
        try:
            cached = await redis_manager.get(cache_key)
            if cached:
                # Ensure we return a dict, not a string (in case of corrupted data)
                if isinstance(cached, dict):
                    return cached
                elif isinstance(cached, str):
                    # Try to parse string as JSON (corrupted data from old bug)
                    import json
                    try:
                        parsed = json.loads(cached)
                        if isinstance(parsed, dict):
                            return parsed
                    except:
                        pass
                logger.warning(f"Invalid cached data type for server {server_id}: {type(cached)}")
            return None
        except Exception as e:
            logger.error(f"Error getting cached A2S info for server {server_id}: {e}")
            return None


# Global instance
a2s_cache_service = A2SCacheService()
