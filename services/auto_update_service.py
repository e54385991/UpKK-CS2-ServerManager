"""
Auto-Update Service for CS2 Servers
Periodically checks server versions against Steam API and triggers updates when needed
"""
import asyncio
import logging
from typing import Optional, Set
from modules.utils import get_current_time

from services.steam_api_service import steam_api_service
from services.ssh_manager import SSHManager
from services.steam_inf_service import steam_inf_service

logger = logging.getLogger(__name__)


class AutoUpdateService:
    """Background service to check and update CS2 servers automatically"""
    
    def __init__(self):
        self.check_interval = 60  # Check every minute (configurable, supports debugging)
        self.task: Optional[asyncio.Task] = None
        self.running = False
        self.updating_servers: Set[int] = set()  # Track servers currently being updated
        self._update_locks: dict[int, asyncio.Lock] = {}  # Per-server locks to prevent concurrent updates
        
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
        from sqlmodel import select, update
        
        try:
            # Fetch server list quickly and close DB connection to avoid pool exhaustion
            async with async_session_maker() as db:
                servers = await Server.get_all_with_auto_update(db)
            
            logger.info(f"Checking {len(servers)} servers with auto-update enabled")
            
            # Check and update each server
            # DB session is already closed, so SSH operations won't hold DB connections
            for server in servers:
                # Skip if server is currently being updated (prevent duplicate runs)
                if server.id in self.updating_servers:
                    logger.debug(
                        f"Skipping server {server.id} ({server.name}) - "
                        f"update already in progress"
                    )
                    continue
                
                # Skip servers that are marked as down due to SSH failures
                if server.should_skip_background_checks():
                    logger.info(f"Skipping auto-update check for server {server.id} ({server.name}) - marked as SSH down for 3+ days")
                    continue
                
                # Check if we should check this server based on its configured interval
                interval_hours = server.update_check_interval_hours or 1
                if not steam_api_service.should_check_version(server.last_update_check, interval_hours):
                    logger.debug(
                        f"Skipping server {server.id} ({server.name}) - "
                        f"checked recently (interval: {interval_hours}h)"
                    )
                    continue
                
                await self._check_and_update_server(server)
                
        except Exception as e:
            logger.error(f"Error checking servers for updates: {e}")
    
    async def _check_and_update_server(self, server):
        """Check a single server and update if needed"""
        try:
            # Wrap the entire check in a timeout to prevent blocking
            # Use 60 seconds timeout for the entire check process
            async def _do_check():
                # Update last check time first - use a separate DB session just for this quick operation
                from modules.database import async_session_maker
                from sqlalchemy import update as sql_update
                from modules.models import Server
                async with async_session_maker() as db:
                    await db.execute(
                        sql_update(Server)
                        .where(Server.id == server.id)
                        .values(last_update_check=get_current_time())
                    )
                    await db.commit()
                
                # Try to get version from steam.inf first (more reliable)
                current_version = None
                version_source = "unknown"
                
                success, version = await steam_inf_service.get_version_from_steam_inf(server)
                
                if success and version:
                    current_version = version
                    version_source = "steam.inf"
                    logger.info(
                        f"Got version from steam.inf for server {server.id} ({server.name}): "
                        f"{current_version}"
                    )
                else:
                    # Fallback to database stored version (from A2S or previous reads)
                    current_version = server.current_game_version
                    version_source = "database/A2S"
                    logger.info(
                        f"steam.inf read failed, using stored version for server {server.id} ({server.name}): "
                        f"current_version={current_version}"
                    )
                
                if not current_version:
                    logger.warning(
                        f"No version available for server {server.id}, skipping update check"
                    )
                    return
                
                # Check version against Steam API
                success, result = await steam_api_service.check_version(current_version)
                
                if not success:
                    logger.warning(
                        f"Failed to check version for server {server.id}: "
                        f"{result.get('error', 'Unknown error')}"
                    )
                    return
                
                # Check if update is needed
                if result.get('up_to_date', True):
                    logger.info(
                        f"Server {server.id} ({server.name}) is up-to-date "
                        f"(version: {current_version} from {version_source})"
                    )
                    return
                
                required_version = result.get('required_version')
                logger.info(
                    f"Server {server.id} ({server.name}) needs update: "
                    f"current={current_version} (from {version_source}), required={required_version}"
                )
                
                # Trigger update
                await self._trigger_server_update(server)
            
            # Apply timeout to prevent blocking the event loop
            await asyncio.wait_for(_do_check(), timeout=60)
            
        except asyncio.TimeoutError:
            logger.warning(f"Timeout checking/updating server {server.id} - operation took longer than 60 seconds, skipping to prevent blocking")
        except Exception as e:
            logger.error(f"Error checking/updating server {server.id}: {e}")
    
    async def _trigger_server_update(self, server):
        """Trigger update for a server and restart it"""
        # Get or create lock for this server
        if server.id not in self._update_locks:
            self._update_locks[server.id] = asyncio.Lock()
        
        lock = self._update_locks[server.id]
        
        # Try to acquire lock without blocking - if already locked, skip this update
        if lock.locked():
            logger.warning(
                f"Server {server.id} ({server.name}) update already in progress, "
                f"skipping duplicate update request"
            )
            return
        
        async with lock:
            # Mark server as being updated
            self.updating_servers.add(server.id)
            
            # Create deployment log for auto-update
            from modules.database import async_session_maker
            from modules.models import DeploymentLog
            
            log_id = None
            try:
                async with async_session_maker() as db:
                    log = DeploymentLog(
                        server_id=server.id,
                        action="auto_update",
                        status="in_progress"
                    )
                    db.add(log)
                    await db.commit()
                    await db.refresh(log)
                    log_id = log.id
                
                logger.info(f"Triggering auto-update for server {server.id} ({server.name})")
                
                # Create SSH manager
                ssh_manager = SSHManager()
                
                # Collect output messages for the log
                output_messages = []
                
                # Define progress callback
                async def log_progress(msg: str):
                    logger.info(f"[Server {server.id}] {msg}")
                    output_messages.append(msg)
                
                await log_progress("Starting auto-update...")
                
                # Run update command (this handles connection, update, and restart if server was running)
                # The update_server method will:
                # 1. Connect to SSH
                # 2. Stop server if running
                # 3. Run SteamCMD update
                # 4. Restart server if it was running before
                # 5. Disconnect SSH
                logger.info(f"Running update on server {server.id}")
                update_success, update_message = await ssh_manager.update_server(
                    server,
                    progress_callback=log_progress
                )
                
                if not update_success:
                    error_msg = f"Update failed: {update_message}"
                    logger.error(f"Update failed for server {server.id}: {update_message}")
                    
                    # Update log as failed
                    async with async_session_maker() as db:
                        log_to_update = await db.get(DeploymentLog, log_id)
                        if log_to_update:
                            log_to_update.status = "failed"
                            log_to_update.error_message = error_msg
                            log_to_update.output = "\n".join(output_messages) if output_messages else None
                            await db.commit()
                    return
                
                logger.info(f"Auto-update completed successfully for server {server.id}")
                await log_progress("Auto-update completed successfully")
                
                # Update last_update_time in database
                from sqlalchemy import update as sql_update
                from modules.models import Server
                async with async_session_maker() as db:
                    await db.execute(
                        sql_update(Server)
                        .where(Server.id == server.id)
                        .values(last_update_time=get_current_time())
                    )
                    await db.commit()
                
                # Update log as success
                async with async_session_maker() as db:
                    log_to_update = await db.get(DeploymentLog, log_id)
                    if log_to_update:
                        log_to_update.status = "success"
                        log_to_update.output = "\n".join(output_messages)
                        await db.commit()
                    
            except Exception as e:
                error_msg = f"Error during auto-update: {str(e)}"
                logger.error(f"Error triggering update for server {server.id}: {e}")
                
                # Update log as failed
                try:
                    async with async_session_maker() as db:
                        if log_id:
                            log_to_update = await db.get(DeploymentLog, log_id)
                            if log_to_update:
                                log_to_update.status = "failed"
                                log_to_update.error_message = error_msg
                                log_to_update.output = "\n".join(output_messages)
                                await db.commit()
                except Exception as log_error:
                    logger.error(f"Failed to update deployment log: {log_error}")
            finally:
                # Remove server from updating set
                self.updating_servers.discard(server.id)


# Global instance
auto_update_service = AutoUpdateService()
