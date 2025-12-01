"""
Server monitoring and auto-restart service
Monitors CS2 servers and automatically restarts them if they crash
Allows up to 5 restarts within 10 minutes to prevent restart loops
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import logging
from modules.utils import get_current_time

logger = logging.getLogger(__name__)


class ServerMonitor:
    """Monitors servers and handles automatic restart on crash"""
    
    def __init__(self):
        # Track restart attempts: server_id -> list of (timestamp, restart_count)
        self.restart_history: Dict[int, List[datetime]] = {}
        self.max_restarts = 5  # Maximum restarts within time window
        self.time_window = timedelta(minutes=10)  # Time window for restart tracking
        self.monitoring_tasks: Dict[int, asyncio.Task] = {}
        # Track consecutive A2S failures: server_id -> failure_count
        self.a2s_failure_count: Dict[int, int] = {}
    
    def can_restart(self, server_id: int) -> Tuple[bool, str]:
        """
        Check if server can be restarted based on restart history
        
        Returns:
            Tuple[bool, str]: (can_restart, reason)
        """
        now = get_current_time()
        
        # Get restart history for this server
        if server_id not in self.restart_history:
            self.restart_history[server_id] = []
        
        # Clean up old restart records (outside time window)
        cutoff_time = now - self.time_window
        self.restart_history[server_id] = [
            timestamp for timestamp in self.restart_history[server_id]
            if timestamp > cutoff_time
        ]
        
        # Check if we're within restart limits
        restart_count = len(self.restart_history[server_id])
        
        if restart_count >= self.max_restarts:
            oldest_restart = min(self.restart_history[server_id])
            retry_after = oldest_restart + self.time_window
            minutes_left = int((retry_after - now).total_seconds() / 60) + 1
            
            return False, (
                f"Server has crashed {restart_count} times in the last 10 minutes. "
                f"Auto-restart disabled to prevent restart loop. "
                f"Manual restart will be available in {minutes_left} minute(s)."
            )
        
        return True, f"Auto-restart available ({restart_count}/{self.max_restarts} used)"
    
    def record_restart(self, server_id: int):
        """Record a restart attempt for a server"""
        now = get_current_time()
        
        if server_id not in self.restart_history:
            self.restart_history[server_id] = []
        
        self.restart_history[server_id].append(now)
        
        restart_count = len(self.restart_history[server_id])
        logger.info(
            f"Recorded restart for server {server_id}. "
            f"Count: {restart_count}/{self.max_restarts} in last 10 minutes"
        )
    
    def reset_restart_history(self, server_id: int):
        """Reset restart history for a server (e.g., after successful manual intervention)"""
        if server_id in self.restart_history:
            self.restart_history[server_id] = []
            logger.info(f"Reset restart history for server {server_id}")
        # Also reset A2S failure counter
        if server_id in self.a2s_failure_count:
            self.a2s_failure_count[server_id] = 0
            logger.info(f"Reset A2S failure counter for server {server_id}")
    
    def get_restart_info(self, server_id: int) -> Dict:
        """Get restart information for a server"""
        now = get_current_time()
        cutoff_time = now - self.time_window
        
        if server_id not in self.restart_history:
            return {
                "restart_count": 0,
                "max_restarts": self.max_restarts,
                "time_window_minutes": 10,
                "can_restart": True,
                "recent_restarts": []
            }
        
        # Clean up old records
        recent_restarts = [
            timestamp for timestamp in self.restart_history[server_id]
            if timestamp > cutoff_time
        ]
        
        can_restart, _ = self.can_restart(server_id)
        
        return {
            "restart_count": len(recent_restarts),
            "max_restarts": self.max_restarts,
            "time_window_minutes": 10,
            "can_restart": can_restart,
            "recent_restarts": [ts.isoformat() for ts in recent_restarts]
        }
    
    async def monitor_server(self, server_id: int, ssh_manager, progress_callback=None):
        """
        Monitor a server and auto-restart on crash
        
        Args:
            server_id: ID of server to monitor
            ssh_manager: SSHManager instance to use for checks and restarts
            progress_callback: Optional callback for progress updates
        """
        from modules.database import async_session_maker
        from modules.models import Server, ServerStatus
        from sqlmodel import select
        from services.redis_manager import redis_manager
        
        logger.info(f"Starting panel-based monitoring for server {server_id}")
        
        # Log monitoring start to Redis
        try:
            success = await redis_manager.append_monitoring_log(
                server_id=server_id,
                event_type='monitoring_start',
                status='info',
                message='Panel monitoring started'
            )
            logger.info(f"Logged monitoring_start to Redis: success={success}")
        except Exception as e:
            logger.error(f"Failed to log monitoring_start to Redis: {e}")
        
        try:
            # Initialize check interval with default value
            check_interval = 60
            
            while True:
                # Get fresh server data from database
                async with async_session_maker() as db:
                    server = await db.get(Server, server_id)
                    
                    if not server:
                        logger.warning(f"Server {server_id} not found in database, stopping monitoring")
                        try:
                            await redis_manager.append_monitoring_log(
                                server_id=server_id,
                                event_type='monitoring_stop',
                                status='warning',
                                message='Monitoring stopped: server not found in database'
                            )
                        except Exception as e:
                            logger.error(f"Failed to log monitoring_stop to Redis: {e}")
                        break
                    
                    # Check if monitoring is still enabled
                    if not server.enable_panel_monitoring:
                        logger.info(f"Monitoring disabled for server {server_id}, stopping")
                        try:
                            await redis_manager.append_monitoring_log(
                                server_id=server_id,
                                event_type='monitoring_stop',
                                status='info',
                                message='Panel monitoring disabled by user'
                            )
                        except Exception as e:
                            logger.error(f"Failed to log monitoring_stop to Redis: {e}")
                        break
                    
                    # Determine check interval based on monitoring type
                    if server.enable_a2s_monitoring:
                        # Use separate A2S check interval  
                        check_interval = server.a2s_check_interval_seconds or 60
                    else:
                        # Use general monitoring interval
                        check_interval = server.monitor_interval_seconds or 60
                    
                    # Update last status check time
                    server.last_status_check = get_current_time()
                    
                    # Determine if server is down based on monitoring type
                    is_down = False
                    check_status = 'success'
                    check_message = 'Server is running'
                    event_type = 'status_check'
                    
                    # A2S monitoring (preferred if enabled)
                    if server.enable_a2s_monitoring:
                        from services.a2s_query import a2s_service
                        
                        # Use configured A2S host/port or fall back to server host/game_port
                        query_host = server.a2s_query_host or server.host
                        query_port = server.a2s_query_port or server.game_port
                        
                        logger.debug(f"Performing A2S query for server {server_id} at {query_host}:{query_port}")
                        
                        event_type = 'a2s_check'
                        
                        try:
                            # Perform A2S health check
                            a2s_success = await a2s_service.check_server_health(query_host, query_port, timeout=5.0)
                            
                            if a2s_success:
                                # Reset failure counter on success
                                if server_id in self.a2s_failure_count:
                                    self.a2s_failure_count[server_id] = 0
                                
                                check_status = 'success'
                                check_message = f'A2S query successful at {query_host}:{query_port}'
                                logger.debug(f"Server {server_id} A2S query successful")
                            else:
                                # Increment failure counter
                                if server_id not in self.a2s_failure_count:
                                    self.a2s_failure_count[server_id] = 0
                                self.a2s_failure_count[server_id] += 1
                                
                                failure_count = self.a2s_failure_count[server_id]
                                threshold = server.a2s_failure_threshold or 3
                                
                                check_status = 'warning'
                                check_message = f'A2S query failed at {query_host}:{query_port} ({failure_count}/{threshold} failures)'
                                logger.warning(f"Server {server_id} A2S query failed: {failure_count}/{threshold}")
                                
                                # Mark as down if threshold exceeded
                                if failure_count >= threshold:
                                    is_down = True
                                    check_status = 'failed'
                                    check_message = f'A2S query failed {failure_count} consecutive times (threshold: {threshold})'
                        except Exception as e:
                            is_down = True
                            check_status = 'failed'
                            check_message = f'A2S query error: {str(e)}'
                            logger.error(f"Server {server_id} A2S query exception: {e}")
                    
                    # Process-based monitoring (if A2S not enabled)
                    elif server.enable_panel_monitoring:
                        # Check server status via SSH
                        try:
                            success, status_msg = await ssh_manager.get_server_status(server)
                            
                            logger.debug(f"Server {server_id} status check: success={success}, status={status_msg}")
                            
                            if not success:
                                is_down = True
                                check_status = 'failed'
                                check_message = f'Status check failed: {status_msg}'
                                logger.warning(f"Server {server_id} status check failed: {status_msg}")
                            elif status_msg == "stopped":
                                is_down = True
                                check_status = 'warning'
                                check_message = 'Server process not found'
                                logger.warning(f"Server {server_id} process not found")
                            else:
                                check_status = 'success'
                                check_message = f'Server is {status_msg}'
                        except Exception as e:
                            is_down = True
                            check_status = 'failed'
                            check_message = f'SSH status check error: {str(e)}'
                            logger.error(f"Server {server_id} SSH status check exception: {e}")
                    
                    # Log status check to Redis
                    try:
                        log_success = await redis_manager.append_monitoring_log(
                            server_id=server_id,
                            event_type=event_type,
                            status=check_status,
                            message=check_message
                        )
                        logger.info(f"Status check logged to Redis: server={server_id}, type={event_type}, status={check_status}, success={log_success}")
                    except Exception as e:
                        logger.error(f"Exception logging status check to Redis: server={server_id}, error={e}")
                    
                    # Handle down server
                    if is_down and server.auto_restart_on_crash:
                        # Check if we can restart (respecting restart limits)
                        can_restart, reason = self.can_restart(server_id)
                        
                        if can_restart:
                            # Determine restart trigger source
                            if server.enable_a2s_monitoring:
                                restart_trigger = f'A2S monitoring ({self.a2s_failure_count.get(server_id, 0)} consecutive failures)'
                                logger.info(f"Auto-restarting server {server_id} due to A2S failures")
                            else:
                                restart_trigger = 'panel monitoring (process check)'
                                logger.info(f"Auto-restarting server {server_id} via panel monitoring")
                            
                            # Log restart attempt to Redis
                            try:
                                await redis_manager.append_monitoring_log(
                                    server_id=server_id,
                                    event_type='auto_restart',
                                    status='info',
                                    message=f'Auto-restart triggered by {restart_trigger}'
                                )
                            except Exception as e:
                                logger.error(f"Failed to log restart trigger to Redis: {e}")
                            
                            # Record restart attempt
                            self.record_restart(server_id)
                            
                            try:
                                # Attempt restart
                                restart_success, restart_msg = await ssh_manager.start_server(server, progress_callback)
                                
                                if restart_success:
                                    logger.info(f"Successfully auto-restarted server {server_id}")
                                    server.status = ServerStatus.RUNNING
                                    
                                    # Log successful restart to Redis
                                    try:
                                        await redis_manager.append_monitoring_log(
                                            server_id=server_id,
                                            event_type='auto_restart',
                                            status='success',
                                            message='Auto-restart completed successfully'
                                        )
                                    except Exception as e:
                                        logger.error(f"Failed to log restart success to Redis: {e}")
                                else:
                                    logger.error(f"Failed to auto-restart server {server_id}: {restart_msg}")
                                    server.status = ServerStatus.ERROR
                                    
                                    # Log failed restart to Redis
                                    try:
                                        await redis_manager.append_monitoring_log(
                                            server_id=server_id,
                                            event_type='auto_restart',
                                            status='failed',
                                            message=f'Auto-restart failed: {restart_msg}'
                                        )
                                    except Exception as e:
                                        logger.error(f"Failed to log restart failure to Redis: {e}")
                            except Exception as e:
                                logger.error(f"Exception during auto-restart of server {server_id}: {e}")
                                server.status = ServerStatus.ERROR
                                
                                # Log restart exception to Redis
                                try:
                                    await redis_manager.append_monitoring_log(
                                        server_id=server_id,
                                        event_type='auto_restart',
                                        status='failed',
                                        message=f'Auto-restart error: {str(e)}'
                                    )
                                except Exception as redis_e:
                                    logger.error(f"Failed to log restart error to Redis: {redis_e}")
                        else:
                            logger.warning(f"Cannot auto-restart server {server_id}: {reason}")
                            server.status = ServerStatus.ERROR
                            
                            # Log blocked restart to Redis
                            try:
                                await redis_manager.append_monitoring_log(
                                    server_id=server_id,
                                    event_type='auto_restart',
                                    status='warning',
                                    message=f'Auto-restart blocked: {reason}'
                                )
                            except Exception as e:
                                logger.error(f"Failed to log blocked restart to Redis: {e}")
                    elif is_down:
                        logger.info(f"Server {server_id} is down but auto-restart is disabled")
                        server.status = ServerStatus.STOPPED
                    else:
                        # Server is running
                        if server.status != ServerStatus.RUNNING:
                            server.status = ServerStatus.RUNNING
                    
                    # Commit status updates
                    await db.commit()
                
                # Wait before next check
                await asyncio.sleep(check_interval)
                    
        except asyncio.CancelledError:
            logger.info(f"Monitoring cancelled for server {server_id}")
            raise
        except Exception as e:
            logger.error(f"Error monitoring server {server_id}: {e}", exc_info=True)
    
    def start_monitoring(self, server_id: int, ssh_manager, progress_callback=None):
        """Start monitoring a server in the background"""
        if server_id in self.monitoring_tasks:
            logger.warning(f"Server {server_id} is already being monitored")
            return
        
        task = asyncio.create_task(
            self.monitor_server(server_id, ssh_manager, progress_callback)
        )
        self.monitoring_tasks[server_id] = task
        logger.info(f"Started monitoring task for server {server_id}")
    
    def stop_monitoring(self, server_id: int):
        """Stop monitoring a server"""
        if server_id in self.monitoring_tasks:
            self.monitoring_tasks[server_id].cancel()
            del self.monitoring_tasks[server_id]
            logger.info(f"Stopped monitoring for server {server_id}")


# Global monitor instance
server_monitor = ServerMonitor()
