"""
Scheduled Task Service for CS2 Servers
Executes scheduled tasks based on configured schedules
"""
import asyncio
import logging
from typing import Optional, Dict
from datetime import datetime, timedelta
import re

from modules.utils import get_current_time
from modules.database import async_session_maker
from modules.models import ScheduledTask, Server
from sqlmodel import select, update as sql_update
from services.ssh_manager import SSHManager

logger = logging.getLogger(__name__)


class ScheduledTaskService:
    """Background service to execute scheduled tasks"""
    
    def __init__(self):
        self.check_interval = 30  # Check every 30 seconds
        self.task: Optional[asyncio.Task] = None
        self.running = False
        # Track running tasks to prevent duplicate execution
        self.running_tasks: Dict[int, asyncio.Task] = {}
        
    async def start(self):
        """Start the background scheduled task service"""
        if self.task is None or self.task.done():
            self.running = True
            self.task = asyncio.create_task(self._execution_loop())
            logger.info("Scheduled task service started")
            # Calculate next run times for all tasks on startup
            await self._calculate_all_next_runs()
    
    def stop(self):
        """Stop the background scheduled task service"""
        self.running = False
        if self.task and not self.task.done():
            self.task.cancel()
        # Cancel all running tasks
        for task_id, task in list(self.running_tasks.items()):
            if not task.done():
                task.cancel()
        self.running_tasks.clear()
        logger.info("Scheduled task service stopped")
    
    async def _execution_loop(self):
        """Main execution loop"""
        while self.running:
            try:
                await self._check_and_execute_tasks()
            except Exception as e:
                logger.error(f"Error in scheduled task loop: {e}")
            
            # Wait for next check
            await asyncio.sleep(self.check_interval)
    
    async def _check_and_execute_tasks(self):
        """Check for tasks that need to be executed"""
        try:
            async with async_session_maker() as db:
                now = get_current_time()
                
                # Get all enabled tasks that are due for execution
                result = await db.execute(
                    select(ScheduledTask)
                    .where(ScheduledTask.enabled.is_(True))
                    .where(
                        (ScheduledTask.next_run.is_(None)) | 
                        (ScheduledTask.next_run <= now)
                    )
                )
                tasks = result.scalars().all()
                
                for task in tasks:
                    # Skip if task is already running
                    if task.id in self.running_tasks and not self.running_tasks[task.id].done():
                        logger.debug(f"Task {task.id} is already running, skipping")
                        continue
                    
                    # Execute task in background
                    logger.info(f"Executing scheduled task {task.id}: {task.name} (action: {task.action})")
                    execution_task = asyncio.create_task(self._execute_task(task))
                    self.running_tasks[task.id] = execution_task
                    
        except Exception as e:
            logger.error(f"Error checking scheduled tasks: {e}")
    
    async def _execute_task(self, task: ScheduledTask):
        """Execute a single scheduled task"""
        try:
            # Get the server
            async with async_session_maker() as db:
                server = await db.get(Server, task.server_id)
                
                if not server:
                    logger.error(f"Server {task.server_id} not found for task {task.id}")
                    await self._update_task_status(
                        task.id, 
                        "failed", 
                        f"Server {task.server_id} not found"
                    )
                    return
            
            # Skip task if server is marked as down due to SSH failures
            if server.should_skip_background_checks():
                logger.info(f"Skipping scheduled task {task.id} for server {server.id} - marked as SSH down for 3+ days")
                await self._update_task_status(
                    task.id,
                    "skipped",
                    "Server marked as SSH down for 3+ consecutive days"
                )
                return
            
            # Create SSH manager using the pattern from main.py
            ssh_manager = SSHManager()
            
            # Connect to server
            connect_success, connect_msg = await ssh_manager.connect(server)
            if not connect_success:
                logger.error(f"Failed to connect to server {server.id} for task {task.id}: {connect_msg}")
                await self._update_task_status(
                    task.id,
                    "failed",
                    f"Failed to connect to server: {connect_msg}"
                )
                return
            
            try:
                # Execute the action
                success, message = await self._execute_action(
                    ssh_manager, 
                    server, 
                    task.action
                )
                
                if success:
                    logger.info(f"Task {task.id} completed successfully")
                    await self._update_task_status(task.id, "success", None)
                else:
                    logger.error(f"Task {task.id} failed: {message}")
                    await self._update_task_status(task.id, "failed", message)
                    
            finally:
                await ssh_manager.disconnect()
                
        except Exception as e:
            logger.error(f"Error executing task {task.id}: {e}")
            await self._update_task_status(task.id, "failed", str(e))
        finally:
            # Remove from running tasks
            if task.id in self.running_tasks:
                del self.running_tasks[task.id]
    
    async def _execute_action(self, ssh_manager: SSHManager, server: Server, action: str):
        """Execute the specified action on the server"""
        try:
            async def log_progress(msg: str):
                logger.info(f"[Server {server.id}] {msg}")
            
            if action == "restart":
                # Restart is implemented as stop + start sequence
                await log_progress("Stopping server...")
                success, message = await ssh_manager.stop_server(server)
                
                # Always proceed to start, even if stop reports failure
                # The start_server method has its own defensive checks
                if not success:
                    await log_progress(f"Stop returned: {message}")
                    await log_progress("Proceeding with start (defensive checks will ensure cleanup)...")
                else:
                    await log_progress("Server stopped successfully, starting again...")
                
                # Add small delay to ensure cleanup
                await asyncio.sleep(0.5)
                
                await log_progress("Starting server...")
                return await ssh_manager.start_server(server, progress_callback=log_progress)
                
            elif action == "start":
                return await ssh_manager.start_server(server, progress_callback=log_progress)
            elif action == "stop":
                return await ssh_manager.stop_server(server)
            elif action == "update":
                return await ssh_manager.update_server(server, progress_callback=log_progress)
            elif action == "validate":
                return await ssh_manager.validate_server(server, progress_callback=log_progress)
            else:
                return False, f"Unknown action: {action}"
                
        except Exception as e:
            return False, str(e)
    
    async def _update_task_status(self, task_id: int, status: str, error: Optional[str]):
        """Update task execution status and calculate next run"""
        try:
            async with async_session_maker() as db:
                # Get the task
                task = await db.get(ScheduledTask, task_id)
                
                if not task:
                    return
                
                # Calculate next run time
                next_run = self._calculate_next_run(task)
                
                # Update task
                await db.execute(
                    sql_update(ScheduledTask)
                    .where(ScheduledTask.id == task_id)
                    .values(
                        last_run=get_current_time(),
                        next_run=next_run,
                        run_count=task.run_count + 1,
                        last_status=status,
                        last_error=error
                    )
                )
                await db.commit()
                
                logger.info(
                    f"Task {task_id} status updated: {status}, next run: {next_run}"
                )
                
        except Exception as e:
            logger.error(f"Error updating task status: {e}")
    
    def _calculate_next_run(self, task: ScheduledTask) -> Optional[datetime]:
        """Calculate next run time based on schedule type and value"""
        try:
            now = get_current_time()
            schedule_type = task.schedule_type
            schedule_value = task.schedule_value
            
            if schedule_type == "daily":
                # Format: "HH:MM" (e.g., "14:30")
                return self._calculate_daily_next_run(now, schedule_value)
                
            elif schedule_type == "weekly":
                # Format: "DAY:HH:MM" (e.g., "MON:14:30", "SUN:08:00")
                return self._calculate_weekly_next_run(now, schedule_value)
                
            elif schedule_type == "interval":
                # Format: seconds as string (e.g., "3600" for 1 hour)
                try:
                    interval_seconds = int(schedule_value)
                    if interval_seconds <= 0:
                        raise ValueError(f"Interval must be positive, got {interval_seconds}")
                    return now + timedelta(seconds=interval_seconds)
                except ValueError as e:
                    logger.error(f"Invalid interval value '{schedule_value}' for task {task.id}: {e}")
                    return None
                
            elif schedule_type == "cron":
                # Simple cron-like format: "MIN HOUR DAY MONTH WEEKDAY"
                # For now, return None (not implemented in this version)
                # Future enhancement: use croniter library
                logger.warning(f"Cron scheduling not yet implemented for task {task.id}")
                return None
                
            else:
                logger.error(f"Unknown schedule type: {schedule_type}")
                return None
                
        except Exception as e:
            logger.error(f"Error calculating next run for task {task.id}: {e}")
            return None
    
    def _calculate_daily_next_run(self, now: datetime, time_str: str) -> datetime:
        """Calculate next run for daily schedule (format: HH:MM)"""
        try:
            # Parse time
            match = re.match(r'^(\d{1,2}):(\d{2})$', time_str)
            if not match:
                raise ValueError(f"Invalid daily time format: {time_str}")
            
            hour = int(match.group(1))
            minute = int(match.group(2))
            
            if hour < 0 or hour > 23:
                raise ValueError(f"Invalid hour: {hour}")
            if minute < 0 or minute > 59:
                raise ValueError(f"Invalid minute: {minute}")
            
            # Calculate next run
            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            # If time has passed today, schedule for tomorrow
            if next_run <= now:
                next_run += timedelta(days=1)
            
            return next_run
            
        except Exception as e:
            logger.error(f"Error parsing daily schedule '{time_str}': {e}")
            raise
    
    def _calculate_weekly_next_run(self, now: datetime, schedule_str: str) -> datetime:
        """Calculate next run for weekly schedule (format: DAY:HH:MM)"""
        try:
            # Parse schedule
            match = re.match(r'^([A-Z]{3}):(\d{1,2}):(\d{2})$', schedule_str.upper())
            if not match:
                raise ValueError(f"Invalid weekly schedule format: {schedule_str}")
            
            day_name = match.group(1)
            hour = int(match.group(2))
            minute = int(match.group(3))
            
            # Map day names to weekday numbers (0=Monday, 6=Sunday)
            day_map = {
                'MON': 0, 'TUE': 1, 'WED': 2, 'THU': 3,
                'FRI': 4, 'SAT': 5, 'SUN': 6
            }
            
            if day_name not in day_map:
                raise ValueError(f"Invalid day: {day_name}")
            if hour < 0 or hour > 23:
                raise ValueError(f"Invalid hour: {hour}")
            if minute < 0 or minute > 59:
                raise ValueError(f"Invalid minute: {minute}")
            
            target_weekday = day_map[day_name]
            current_weekday = now.weekday()
            
            # Calculate days until target day
            days_ahead = target_weekday - current_weekday
            if days_ahead < 0:  # Target day already happened this week
                days_ahead += 7
            elif days_ahead == 0:  # Same day
                # Check if time has passed
                next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if next_run <= now:
                    days_ahead = 7
            
            # Calculate next run
            next_run = now + timedelta(days=days_ahead)
            next_run = next_run.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            return next_run
            
        except Exception as e:
            logger.error(f"Error parsing weekly schedule '{schedule_str}': {e}")
            raise
    
    async def _calculate_all_next_runs(self):
        """Calculate next run times for all enabled tasks on startup"""
        try:
            async with async_session_maker() as db:
                # Get all enabled tasks
                result = await db.execute(
                    select(ScheduledTask).where(ScheduledTask.enabled.is_(True))
                )
                tasks = result.scalars().all()
                
                for task in tasks:
                    try:
                        next_run = self._calculate_next_run(task)
                        if next_run:
                            await db.execute(
                                sql_update(ScheduledTask)
                                .where(ScheduledTask.id == task.id)
                                .values(next_run=next_run)
                            )
                            logger.info(
                                f"Calculated next run for task {task.id}: {next_run}"
                            )
                    except Exception as e:
                        logger.error(
                            f"Error calculating next run for task {task.id}: {e}"
                        )
                
                await db.commit()
                logger.info(f"Calculated next run times for {len(tasks)} tasks")
                
        except Exception as e:
            logger.error(f"Error calculating next runs: {e}")
    
    async def recalculate_next_run(self, task_id: int):
        """Recalculate next run time for a specific task (used when task is updated)"""
        try:
            async with async_session_maker() as db:
                task = await db.get(ScheduledTask, task_id)
                
                if not task:
                    return
                
                next_run = self._calculate_next_run(task)
                if next_run:
                    await db.execute(
                        sql_update(ScheduledTask)
                        .where(ScheduledTask.id == task_id)
                        .values(next_run=next_run)
                    )
                    await db.commit()
                    logger.info(f"Recalculated next run for task {task_id}: {next_run}")
                    
        except Exception as e:
            logger.error(f"Error recalculating next run for task {task_id}: {e}")


# Global instance
scheduled_task_service = ScheduledTaskService()
