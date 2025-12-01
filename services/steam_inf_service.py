"""
Steam.inf Version Cache Service
Reads PatchVersion from cs2/game/csgo/steam.inf file instead of relying on A2S protocol
Provides more stable version information for auto-update triggers
"""
import asyncio
import logging
import re
import shlex
from typing import Optional, Tuple

from services.redis_manager import redis_manager
from services.ssh_manager import SSHManager
from modules.models import Server

logger = logging.getLogger(__name__)


class SteamInfService:
    """Service to read and cache CS2 version from steam.inf file"""
    
    # Cache TTL: 365 days (long-term cache, refreshed on operations and periodically)
    CACHE_TTL_SECONDS = 365 * 24 * 60 * 60
    
    # Periodic refresh interval: 24 hours
    REFRESH_INTERVAL_SECONDS = 24 * 60 * 60
    
    def __init__(self):
        # Cache is long-term - refreshed on server operations or periodic refresh
        self.refresh_interval = self.REFRESH_INTERVAL_SECONDS
        self.refresh_task: Optional[asyncio.Task] = None
        self.running = False
        
    async def start(self):
        """Start periodic refresh task"""
        if self.refresh_task is None or self.refresh_task.done():
            self.running = True
            self.refresh_task = asyncio.create_task(self._refresh_loop())
            logger.info("Steam.inf periodic refresh started (every 24 hours)")
    
    def stop(self):
        """Stop periodic refresh task"""
        self.running = False
        if self.refresh_task and not self.refresh_task.done():
            self.refresh_task.cancel()
            logger.info("Steam.inf periodic refresh stopped")
    
    async def _refresh_loop(self):
        """Periodic refresh loop"""
        while self.running:
            try:
                await self._periodic_refresh_all()
            except Exception as e:
                logger.error(f"Error in steam.inf periodic refresh: {e}")
            
            # Wait for next interval
            await asyncio.sleep(self.refresh_interval)
    
    async def _periodic_refresh_all(self):
        """Periodically refresh all servers' version cache"""
        from modules.database import async_session_maker
        from sqlmodel import select
        
        try:
            async with async_session_maker() as db:
                # Get all servers
                result = await db.execute(select(Server))
                servers = result.scalars().all()
                
                logger.info(f"Periodic refresh: Updating steam.inf version for {len(servers)} servers")
                
                # Refresh each server's version
                for server in servers:
                    try:
                        success, version = await self.get_version_from_steam_inf(server, force_refresh=True)
                        if success:
                            logger.debug(f"Refreshed version for server {server.id}: {version}")
                    except Exception as e:
                        logger.error(f"Error refreshing version for server {server.id}: {e}")
                        
        except Exception as e:
            logger.error(f"Error in periodic refresh: {e}")
        
    async def get_version_from_steam_inf(self, server: Server, force_refresh: bool = False) -> Tuple[bool, Optional[str]]:
        """
        Get CS2 version from steam.inf file
        
        Args:
            server: Server instance
            force_refresh: If True, bypass cache and read from file
            
        Returns:
            Tuple[bool, Optional[str]]: (success, version_string)
            version_string format: "1.41.2.6" or None if failed
        """
        cache_key = f"steam_inf:version:{server.id}"
        
        # Try cache first unless force_refresh
        if not force_refresh:
            cached_version = await redis_manager.get(cache_key)
            if cached_version:
                logger.debug(f"Using cached steam.inf version for server {server.id}: {cached_version}")
                return True, cached_version
            else:
                # Cache is missing, proactively refresh it
                logger.info(f"Cache missing for server {server.id}, proactively refreshing...")
                force_refresh = True
        
        # Read from file (either forced or cache was missing)
        if force_refresh:
            success, version = await self._read_version_from_file(server)
            
            if success and version:
                # Cache the version with 365-day TTL (effectively unlimited)
                await redis_manager.set(cache_key, version, expire=self.CACHE_TTL_SECONDS)
                logger.info(f"Cached steam.inf version for server {server.id}: {version} (unlimited TTL, periodic refresh enabled)")
                return True, version
        
        return False, None
    
    async def _read_version_from_file(self, server: Server) -> Tuple[bool, Optional[str]]:
        """
        Read PatchVersion from steam.inf file via SSH
        
        Args:
            server: Server instance
            
        Returns:
            Tuple[bool, Optional[str]]: (success, version_string)
        """
        ssh_manager = SSHManager()
        
        try:
            # Connect to server
            success, msg = await ssh_manager.connect(server)
            if not success:
                logger.warning(f"Failed to connect to server {server.id} for steam.inf read: {msg}")
                return False, None
            
            # Path to steam.inf file
            steam_inf_path = f"{server.game_directory}/cs2/game/csgo/steam.inf"
            
            # Properly escape the path for shell command
            escaped_path = shlex.quote(steam_inf_path)
            
            # Check if file exists
            check_cmd = f"test -f {escaped_path} && echo 'exists' || echo 'missing'"
            success, stdout, stderr = await ssh_manager.execute_command(check_cmd)
            
            if not success or 'missing' in stdout:
                logger.warning(f"steam.inf file not found for server {server.id} at {steam_inf_path}")
                return False, None
            
            # Read the file and extract PatchVersion
            # Use grep to find the line with PatchVersion
            read_cmd = f"grep 'PatchVersion=' {escaped_path}"
            success, stdout, stderr = await ssh_manager.execute_command(read_cmd)
            
            if not success or not stdout:
                logger.warning(f"Failed to read PatchVersion from steam.inf for server {server.id}")
                return False, None
            
            # Parse the version from output
            # Expected format: PatchVersion=1.41.2.6
            version = self._parse_patch_version(stdout)
            
            if version:
                logger.info(f"Read version from steam.inf for server {server.id}: {version}")
                return True, version
            else:
                logger.warning(f"Could not parse PatchVersion from steam.inf for server {server.id}: {stdout}")
                return False, None
                
        except Exception as e:
            logger.error(f"Error reading steam.inf for server {server.id}: {e}")
            return False, None
        finally:
            await ssh_manager.disconnect()
    
    def _parse_patch_version(self, output: str) -> Optional[str]:
        """
        Parse PatchVersion from grep output
        
        Args:
            output: Output from grep command
            
        Returns:
            Version string (e.g., "1.41.2.6") or None
        """
        # Match PatchVersion=X.X.X.X pattern
        match = re.search(r'PatchVersion=(\d+\.\d+\.\d+\.\d+)', output)
        if match:
            return match.group(1)
        return None
    
    async def refresh_version_cache(self, server: Server) -> Tuple[bool, Optional[str]]:
        """
        Force refresh version cache by reading from file
        Should be called after server start/restart/update/verify operations
        Also updates the database current_game_version field
        
        Args:
            server: Server instance
            
        Returns:
            Tuple[bool, Optional[str]]: (success, version_string)
        """
        logger.info(f"Refreshing steam.inf version cache for server {server.id}")
        success, version = await self.get_version_from_steam_inf(server, force_refresh=True)
        
        # Update database current_game_version if we successfully got the version
        if success and version:
            try:
                from modules.database import async_session_maker
                from sqlalchemy import update
                from modules.models import Server as ServerModel
                
                async with async_session_maker() as db:
                    # Only update if the version is different
                    result = await db.execute(
                        update(ServerModel)
                        .where(ServerModel.id == server.id)
                        .values(current_game_version=version)
                    )
                    await db.commit()
                    logger.info(f"Updated server {server.id} database version to {version}")
            except Exception as e:
                logger.error(f"Failed to update server version in database: {e}")
        
        return success, version
    
    async def clear_version_cache(self, server_id: int):
        """
        Clear cached version for a server
        
        Args:
            server_id: Server ID
        """
        cache_key = f"steam_inf:version:{server_id}"
        await redis_manager.delete(cache_key)
        logger.debug(f"Cleared steam.inf version cache for server {server_id}")


# Global instance
steam_inf_service = SteamInfService()
