"""
SSH Health Monitoring Daemon Service

This service runs in the background and periodically checks SSH connectivity 
to all servers with enable_ssh_health_monitoring=True.

Features:
- Completely independent background thread/daemon
- Non-blocking checks with configurable intervals
- Tracks consecutive failures
- Automatic recovery from "degraded" state when server becomes available
- Marks as "completely_down" after threshold failures
- No requests to "completely_down" servers unless manually reconnected
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional
import asyncssh
from modules.utils import get_current_time

logger = logging.getLogger(__name__)


class SSHHealthMonitor:
    """Background daemon for SSH health monitoring"""
    
    def __init__(self):
        self.running = False
        self.monitor_task: Optional[asyncio.Task] = None
        # Track last check time per server to avoid duplicate checks
        self.last_check_times: Dict[int, datetime] = {}
        
    async def start(self):
        """Start the SSH health monitoring daemon"""
        if self.running:
            logger.warning("SSH health monitor is already running")
            return
        
        self.running = True
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("SSH health monitoring daemon started")
    
    def stop(self):
        """Stop the SSH health monitoring daemon"""
        if not self.running:
            return
        
        self.running = False
        if self.monitor_task:
            self.monitor_task.cancel()
        logger.info("SSH health monitoring daemon stopped")
    
    async def _monitor_loop(self):
        """Main monitoring loop - runs continuously"""
        try:
            while self.running:
                try:
                    await self._check_all_servers()
                except Exception as e:
                    logger.error(f"Error in SSH health monitoring loop: {e}", exc_info=True)
                
                # Wait 60 seconds before next iteration
                # Each iteration checks which servers need checking based on their intervals
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info("SSH health monitoring loop cancelled")
            raise
    
    async def _check_all_servers(self):
        """Check all servers that need SSH health monitoring"""
        from modules.database import async_session_maker
        from modules.models import Server
        from sqlmodel import select
        
        try:
            async with async_session_maker() as db:
                # Get all servers with SSH health monitoring enabled
                result = await db.execute(
                    select(Server).where(Server.enable_ssh_health_monitoring == True)
                )
                servers = result.scalars().all()
                
                if not servers:
                    return
                
                logger.debug(f"SSH health monitor: checking {len(servers)} server(s)")
                
                # Check each server
                for server in servers:
                    try:
                        await self._check_server_health(server)
                    except Exception as e:
                        logger.error(f"Error checking SSH health for server {server.id}: {e}")
        except Exception as e:
            logger.error(f"Error getting servers for SSH health check: {e}")
    
    async def _check_server_health(self, server):
        """Check SSH health for a single server"""
        from modules.database import async_session_maker
        from modules.models import Server
        from sqlalchemy import update as sql_update
        
        now = get_current_time()
        
        # Calculate when the next check should occur
        check_interval_hours = server.ssh_health_check_interval_hours or 2
        check_interval = timedelta(hours=check_interval_hours)
        
        # Determine if it's time to check this server
        if server.last_ssh_health_check:
            # Make sure we can compare datetimes (handle timezone-naive from DB)
            last_check = server.last_ssh_health_check
            if last_check.tzinfo is None:
                # Database datetime is naive, make it aware using the same timezone as 'now'
                last_check = last_check.replace(tzinfo=now.tzinfo)
            
            next_check_time = last_check + check_interval
            if now < next_check_time:
                # Not time yet
                return
        
        # Avoid checking if we just checked recently (within last 30 seconds)
        # This prevents duplicate checks when multiple monitor cycles run
        if server.id in self.last_check_times:
            last_check = self.last_check_times[server.id]
            # Ensure both datetimes are comparable
            if last_check.tzinfo is None and now.tzinfo is not None:
                last_check = last_check.replace(tzinfo=now.tzinfo)
            elif last_check.tzinfo is not None and now.tzinfo is None:
                now = now.replace(tzinfo=last_check.tzinfo)
            
            if (now - last_check).total_seconds() < 30:
                return
        
        # Skip completely down servers (they need manual reconnect)
        if server.ssh_health_status == "completely_down":
            logger.debug(f"Server {server.id} is completely down, skipping automatic check")
            return
        
        # Mark that we're checking this server
        self.last_check_times[server.id] = now
        
        logger.info(f"Performing SSH health check for server {server.id} ({server.name})")
        
        # Perform SSH connectivity test
        ssh_success = await self._test_ssh_connection(server)
        
        # Update database based on result
        async with async_session_maker() as db:
            # Refresh server data
            server_to_update = await db.get(Server, server.id)
            if not server_to_update:
                return
            
            old_status = server_to_update.ssh_health_status
            old_failures = server_to_update.consecutive_ssh_failures
            
            if ssh_success:
                # SSH connection successful - update to healthy status
                new_status = "healthy"
                new_failures = 0
                
                await db.execute(
                    sql_update(Server)
                    .where(Server.id == server.id)
                    .values(
                        last_ssh_success=now,
                        last_ssh_health_check=now,
                        consecutive_ssh_failures=0,
                        is_ssh_down=False,
                        ssh_health_status="healthy"
                    )
                )
                
                # Log recovery if status changed
                if old_status != "healthy":
                    logger.info(
                        f"Server {server.id} SSH health recovered: {old_status} -> healthy "
                        f"(was {old_failures} failures)"
                    )
            else:
                # SSH connection failed - increment failure count
                new_failures = server_to_update.consecutive_ssh_failures + 1
                threshold = server_to_update.ssh_health_failure_threshold or 84
                
                # Determine new status based on failure count
                if new_failures >= threshold:
                    new_status = "completely_down"
                    is_down = True
                elif new_failures >= 3:
                    new_status = "down"
                    is_down = True
                else:
                    new_status = "degraded"
                    is_down = False
                
                await db.execute(
                    sql_update(Server)
                    .where(Server.id == server.id)
                    .values(
                        last_ssh_failure=now,
                        last_ssh_health_check=now,
                        consecutive_ssh_failures=new_failures,
                        is_ssh_down=is_down,
                        ssh_health_status=new_status
                    )
                )
                
                # Log status changes
                if old_status != new_status:
                    logger.warning(
                        f"Server {server.id} SSH health degraded: {old_status} -> {new_status} "
                        f"(failures: {new_failures}/{threshold})"
                    )
                else:
                    logger.info(
                        f"Server {server.id} SSH health check failed "
                        f"(failures: {new_failures}/{threshold}, status: {new_status})"
                    )
                
                # Special log when reaching completely_down
                if new_status == "completely_down" and old_status != "completely_down":
                    logger.error(
                        f"Server {server.id} marked as COMPLETELY DOWN after {new_failures} "
                        f"consecutive failures. Manual reconnection required."
                    )
            
            await db.commit()
    
    async def _test_ssh_connection(self, server) -> bool:
        """
        Test SSH connection to a server
        
        Returns:
            bool: True if connection successful, False otherwise
        """
        try:
            # Attempt SSH connection with short timeout
            if server.is_password_auth:
                conn = await asyncio.wait_for(
                    asyncssh.connect(
                        host=server.host,
                        port=server.ssh_port,
                        username=server.ssh_user,
                        password=server.ssh_password,
                        known_hosts=None,
                        connect_timeout=10
                    ),
                    timeout=15
                )
            elif server.is_key_auth:
                conn = await asyncio.wait_for(
                    asyncssh.connect(
                        host=server.host,
                        port=server.ssh_port,
                        username=server.ssh_user,
                        client_keys=[server.ssh_key_path],
                        known_hosts=None,
                        connect_timeout=10
                    ),
                    timeout=15
                )
            else:
                # Neither password nor key auth - this shouldn't happen
                logger.error(f"Server {server.id} has no valid authentication method configured")
                return False
            
            # Connection successful, close it
            conn.close()
            await conn.wait_closed()
            return True
            
        except asyncio.TimeoutError:
            logger.debug(f"SSH health check timeout for server {server.id}")
            return False
        except asyncssh.PermissionDenied:
            logger.debug(f"SSH authentication failed for server {server.id}")
            return False
        except Exception as e:
            logger.debug(f"SSH health check failed for server {server.id}: {e}")
            return False
    
    async def manual_reconnect(self, server_id: int) -> tuple[bool, str]:
        """
        Manually reconnect to a completely down server and reset its health status
        
        This is used from the admin UI to restore a completely_down server.
        
        Returns:
            tuple[bool, str]: (success, message)
        """
        from modules.database import async_session_maker
        from modules.models import Server
        from sqlalchemy import update as sql_update
        
        async with async_session_maker() as db:
            server = await db.get(Server, server_id)
            if not server:
                return False, f"Server {server_id} not found"
            
            # Test connection
            logger.info(f"Manual reconnect attempt for server {server_id}")
            ssh_success = await self._test_ssh_connection(server)
            
            now = get_current_time()
            
            if ssh_success:
                # Reset health status to healthy
                await db.execute(
                    sql_update(Server)
                    .where(Server.id == server_id)
                    .values(
                        last_ssh_success=now,
                        last_ssh_health_check=now,
                        consecutive_ssh_failures=0,
                        is_ssh_down=False,
                        ssh_health_status="healthy"
                    )
                )
                await db.commit()
                
                # Clear cached last check time
                if server_id in self.last_check_times:
                    del self.last_check_times[server_id]
                
                logger.info(f"Server {server_id} manually reconnected and reset to healthy")
                return True, "SSH connection successful - server health restored"
            else:
                logger.warning(f"Manual reconnect failed for server {server_id}")
                return False, "SSH connection failed - server is still unreachable"


# Global singleton instance
ssh_health_monitor = SSHHealthMonitor()
