"""
Auto-Update Service for CS2 Servers
Periodically checks server versions against Steam API and triggers updates when needed
"""
import asyncio
import logging
from typing import Optional
from datetime import datetime, timezone

from services.steam_api_service import steam_api_service
from services.ssh_manager import SSHManager

logger = logging.getLogger(__name__)


class AutoUpdateService:
    """Background service to check and update CS2 servers automatically"""
    
    def __init__(self):
        self.check_interval = 3600  # Check every hour
        self.task: Optional[asyncio.Task] = None
        self.running = False
        
    async def start(self):
        """Start the background auto-update task"""
        if self.task is None or self.task.done():
            self.running = True
            self.task = asyncio.create_task(self._update_loop())
            logger.info("Auto-update service started")
            # Do an immediate first check
            await self._check_and_update_servers()
    
    def stop(self):
        """Stop the background auto-update task"""
        self.running = False
        if self.task and not self.task.done():
            self.task.cancel()
            logger.info("Auto-update service stopped")
    
    async def _update_loop(self):
        """Main update check loop"""
        while self.running:
            try:
                await self._check_and_update_servers()
            except Exception as e:
                logger.error(f"Error in auto-update loop: {e}")
            
            # Wait for next interval
            await asyncio.sleep(self.check_interval)
    
    async def _check_and_update_servers(self):
        """Check all servers with auto-update enabled and update if needed"""
        from modules.database import async_session_maker
        from modules.models import Server
        from sqlalchemy import select, update
        
        try:
            async with async_session_maker() as db:
                # Get all servers with auto-update enabled
                result = await db.execute(
                    select(Server).filter(Server.enable_auto_update == True)
                )
                servers = result.scalars().all()
                
                logger.info(f"Checking {len(servers)} servers with auto-update enabled")
                
                # Check and update each server
                for server in servers:
                    # Check if we should check this server based on its configured interval
                    interval_hours = server.update_check_interval_hours or 1
                    if not steam_api_service.should_check_version(server.last_update_check, interval_hours):
                        logger.debug(
                            f"Skipping server {server.id} ({server.name}) - "
                            f"checked recently (interval: {interval_hours}h)"
                        )
                        continue
                    
                    await self._check_and_update_server(server, db)
                    
        except Exception as e:
            logger.error(f"Error checking servers for updates: {e}")
    
    async def _check_and_update_server(self, server, db):
        """Check a single server and update if needed"""
        try:
            logger.info(
                f"Checking version for server {server.id} ({server.name}): "
                f"current_version={server.current_game_version}"
            )
            
            # Update last check time first
            from sqlalchemy import update as sql_update
            from modules.models import Server
            await db.execute(
                sql_update(Server)
                .where(Server.id == server.id)
                .values(last_update_check=datetime.now(timezone.utc))
            )
            await db.commit()
            
            # Check version against Steam API
            success, result = await steam_api_service.check_version(server.current_game_version)
            
            if not success:
                logger.warning(
                    f"Failed to check version for server {server.id}: "
                    f"{result.get('error', 'Unknown error')}"
                )
                return
            
            # Check if update is needed
            if result.get('up_to_date', True):
                logger.info(f"Server {server.id} ({server.name}) is up-to-date")
                return
            
            required_version = result.get('required_version')
            logger.info(
                f"Server {server.id} ({server.name}) needs update: "
                f"current={server.current_game_version}, required={required_version}"
            )
            
            # Trigger update
            await self._trigger_server_update(server)
            
        except Exception as e:
            logger.error(f"Error checking/updating server {server.id}: {e}")
    
    async def _trigger_server_update(self, server):
        """Trigger update for a server and restart it"""
        try:
            logger.info(f"Triggering auto-update for server {server.id} ({server.name})")
            
            # Create SSH manager
            ssh_manager = SSHManager(
                host=server.host,
                port=server.ssh_port,
                username=server.ssh_user,
                password=server.ssh_password if server.auth_type.value == 'password' else None,
                key_path=server.ssh_key_path if server.auth_type.value == 'key_file' else None
            )
            
            # Define progress callback
            async def log_progress(msg: str):
                logger.info(f"[Server {server.id}] {msg}")
            
            # Connect to server
            connect_success = await ssh_manager.connect()
            if not connect_success:
                logger.error(f"Failed to connect to server {server.id} for auto-update")
                return
            
            try:
                # Run update command
                logger.info(f"Running update on server {server.id}")
                update_success, update_message = await ssh_manager.update_server(
                    server.game_directory,
                    send_progress=log_progress
                )
                
                if not update_success:
                    logger.error(
                        f"Update failed for server {server.id}: {update_message}"
                    )
                    return
                
                logger.info(f"Update successful for server {server.id}")
                
                # Update last_update_time in database
                from modules.database import async_session_maker
                from sqlalchemy import update as sql_update
                from modules.models import Server
                async with async_session_maker() as db:
                    await db.execute(
                        sql_update(Server)
                        .where(Server.id == server.id)
                        .values(last_update_time=datetime.now(timezone.utc))
                    )
                    await db.commit()
                
                # Wait a moment before restart
                await asyncio.sleep(2)
                
                # Restart the server
                logger.info(f"Restarting server {server.id} after update")
                restart_success, restart_message = await ssh_manager.restart_server(
                    server.game_directory,
                    send_progress=log_progress
                )
                
                if restart_success:
                    logger.info(
                        f"Auto-update completed successfully for server {server.id}"
                    )
                else:
                    logger.error(
                        f"Restart failed after update for server {server.id}: "
                        f"{restart_message}"
                    )
                    
            finally:
                await ssh_manager.disconnect()
                
        except Exception as e:
            logger.error(f"Error triggering update for server {server.id}: {e}")


# Global instance
auto_update_service = AutoUpdateService()
