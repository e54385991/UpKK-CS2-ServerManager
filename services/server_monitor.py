"""
Server monitoring and auto-restart service
Monitors CS2 servers and automatically restarts them if they crash
Allows up to 5 restarts within each server's protection window
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from modules.utils import get_current_time
from services.discord_notification_service import EVENT_CRASH_RESTART, discord_notification_service
from services.maintenance_lock import OperationBusyError, maintenance_lock_service
from services.restart_protection import (
    DEFAULT_RESTART_PROTECTION_HOURS,
    restart_protection_window,
)
from services.server_lifecycle_policy import (
    automatic_start_block_reason,
)

from .server_monitor_loop import MonitorDependencies
from .server_monitor_loop import monitor_server as _delegated_monitor_server

logger = logging.getLogger(__name__)


def _window_hours(window: timedelta) -> int:
    return max(1, int(window.total_seconds() // 3600))


def _window_label(window: timedelta) -> str:
    seconds = int(window.total_seconds())
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600} hour(s)"
    return f"{max(1, seconds // 60)} minute(s)"


class ServerMonitor:
    """Monitors servers and handles automatic restart on crash"""

    def __init__(self):
        # Track restart attempts: server_id -> list of (timestamp, restart_count)
        self.restart_history: Dict[int, List[datetime]] = {}
        self.max_restarts = 5  # Maximum restarts within time window
        # Legacy global knob. Live checks use each server's restart_protection_hours.
        self.time_window = timedelta(hours=DEFAULT_RESTART_PROTECTION_HOURS)
        self.monitoring_tasks: Dict[int, asyncio.Task] = {}
        # Track consecutive A2S failures: server_id -> failure_count
        self.a2s_failure_count: Dict[int, int] = {}
        # Avoid repeating the same pause message on every monitoring interval.
        self.manual_stop_suppressed: set[int] = set()
        # One Discord notice per protection episode. The 10-minute crash cooldown
        # must not re-send "auto-restart blocked" while this window is still open.
        self.restart_block_notified_until: Dict[int, datetime] = {}

    def can_restart(
        self,
        server_id: int,
        *,
        window: timedelta | None = None,
    ) -> Tuple[bool, str]:
        """
        Check if server can be restarted based on restart history

        Returns:
            Tuple[bool, str]: (can_restart, reason)
        """
        now = get_current_time()
        active_window = window or timedelta(hours=DEFAULT_RESTART_PROTECTION_HOURS)

        # Get restart history for this server
        if server_id not in self.restart_history:
            self.restart_history[server_id] = []

        # Clean up old restart records (outside time window)
        cutoff_time = now - active_window
        self.restart_history[server_id] = [
            timestamp for timestamp in self.restart_history[server_id] if timestamp > cutoff_time
        ]

        # Check if we're within restart limits
        restart_count = len(self.restart_history[server_id])

        if restart_count >= self.max_restarts:
            oldest_restart = min(self.restart_history[server_id])
            retry_after = oldest_restart + active_window
            minutes_left = int((retry_after - now).total_seconds() / 60) + 1
            window_label = _window_label(active_window)

            return False, (
                f"Server has crashed {restart_count} times in the last {window_label}. "
                f"Auto-restart stays disabled until this window expires in {minutes_left} minute(s). "
                f"A manual start, stop, restart, or update clears this counter immediately."
            )

        return True, f"Auto-restart available ({restart_count}/{self.max_restarts} used)"

    def record_restart(self, server_id: int, *, window: timedelta | None = None):
        """Record a restart attempt for a server"""
        now = get_current_time()
        active_window = window or timedelta(hours=DEFAULT_RESTART_PROTECTION_HOURS)

        if server_id not in self.restart_history:
            self.restart_history[server_id] = []

        self.restart_history[server_id].append(now)

        restart_count = len(self.restart_history[server_id])
        logger.info(
            f"Recorded restart for server {server_id}. "
            f"Count: {restart_count}/{self.max_restarts} in last {_window_label(active_window)}"
        )

    def reset_restart_history(self, server_id: int):
        """Reset restart history for a server (e.g., after successful manual intervention)"""
        if server_id in self.restart_history:
            self.restart_history[server_id] = []
            logger.info(f"Reset restart history for server {server_id}")
        self.restart_block_notified_until.pop(server_id, None)
        # Also reset A2S failure counter
        if server_id in self.a2s_failure_count:
            self.a2s_failure_count[server_id] = 0
            logger.info(f"Reset A2S failure counter for server {server_id}")

    def get_restart_info(self, server_id: int, *, window: timedelta | None = None) -> Dict:
        """Get restart information for a server"""
        now = get_current_time()
        active_window = window or timedelta(hours=DEFAULT_RESTART_PROTECTION_HOURS)
        hours = _window_hours(active_window)
        minutes = max(1, int(active_window.total_seconds() // 60))

        if server_id not in self.restart_history:
            return {
                "restart_count": 0,
                "max_restarts": self.max_restarts,
                "time_window_minutes": minutes,
                "protection_window_hours": hours,
                "protection_minutes_remaining": 0,
                "can_restart": True,
                "recent_restarts": [],
            }

        can_restart, _ = self.can_restart(server_id, window=active_window)
        recent_restarts = list(self.restart_history.get(server_id, []))
        remaining = 0
        if not can_restart and recent_restarts:
            retry_after = min(recent_restarts) + active_window
            remaining = max(0, int((retry_after - now).total_seconds() / 60) + 1)

        return {
            "restart_count": len(recent_restarts),
            "max_restarts": self.max_restarts,
            "time_window_minutes": minutes,
            "protection_window_hours": hours,
            "protection_minutes_remaining": remaining,
            "can_restart": can_restart,
            "recent_restarts": [ts.isoformat() for ts in recent_restarts],
        }

    def queue_restart_notification(
        self,
        server,
        *,
        success: bool,
        title: str,
        message: str,
        trigger: str,
        details=None,
        cooldown_label: str | None = None,
        rate_limit_minutes: int | None = None,
        rate_limit_scope: str | None = None,
    ) -> bool:
        """Queue a rate-limited Discord notification for crash recovery events."""
        interval = int(server.discord_crash_restart_min_interval_minutes or 10)
        notify_details = {
            "Trigger": trigger,
            "Cooldown": cooldown_label or f"{interval} minute(s)",
        }
        if details:
            notify_details.update(details)

        return discord_notification_service.queue_notify(
            server,
            EVENT_CRASH_RESTART,
            "auto_restart",
            success,
            message,
            title=title,
            details=notify_details,
            rate_limit_minutes=interval if rate_limit_minutes is None else rate_limit_minutes,
            rate_limit_scope=rate_limit_scope or "auto_restart",
        )

    def _claim_restart_block_notice(self, server_id: int, *, window: timedelta) -> int | None:
        """Return silence minutes for one protection notice, or None while it is held.

        The hold lasts until this episode expires. A manual restart clears it
        immediately so the next loop can notify again.
        """
        now = get_current_time()
        until = self.restart_block_notified_until.get(server_id)
        if until is not None and now < until:
            return None

        info = self.get_restart_info(server_id, window=window)
        remaining = int(info.get("protection_minutes_remaining") or 0)
        if remaining <= 0:
            remaining = max(1, int(window.total_seconds() // 60))
        self.restart_block_notified_until[server_id] = now + timedelta(minutes=remaining)
        return remaining

    def _queue_restart_block_notification(
        self,
        server,
        *,
        message: str,
        check_message: str,
    ) -> bool:
        window = restart_protection_window(server)
        remaining = self._claim_restart_block_notice(server.id, window=window)
        if remaining is None:
            return False
        return self.queue_restart_notification(
            server,
            success=False,
            title="Auto-restart blocked",
            message=message,
            trigger="restart loop protection",
            details={"Health Check": check_message},
            cooldown_label=_window_label(window),
            rate_limit_minutes=remaining,
            rate_limit_scope="restart_loop_protection",
        )

    async def _perform_guarded_restart(
        self,
        server_id: int,
        ssh_manager,
        progress_callback=None,
    ):
        """Re-check policy under the server lock, then perform one restart.

        ``None`` as the first return value means the restart was skipped rather
        than attempted, so callers must not turn an intentional stop into an
        error state or failure notification.
        """
        from modules.database import async_session_maker
        from modules.models import Server
        from services.plugins.diagnostic_policy import has_diagnostic_blocker

        try:
            async with maintenance_lock_service.get(
                server_id,
                operation="monitor:auto_restart",
                wait=False,
                ttl=900,
            ):
                async with async_session_maker() as db:
                    current_server = await db.get(Server, server_id)
                    if current_server is None:
                        return None, "Server no longer exists", None

                    block_reason = automatic_start_block_reason(current_server)
                    if block_reason:
                        return None, block_reason, current_server
                    if not current_server.enable_panel_monitoring:
                        return None, "Panel monitoring was disabled", current_server
                    if not current_server.auto_restart_on_crash:
                        return None, "Automatic restart was disabled", current_server
                    if await has_diagnostic_blocker(server_id, db):
                        return (
                            None,
                            "Auto-restart paused by plugin diagnostic quarantine",
                            current_server,
                        )

                self.record_restart(
                    server_id,
                    window=restart_protection_window(current_server),
                )
                (
                    manager_ready,
                    preflight_message,
                ) = await ssh_manager.check_session_manager_available(current_server)
                if not manager_ready:
                    return (
                        False,
                        f"Auto-restart aborted before stopping: {preflight_message}. "
                        "The existing game session was left untouched.",
                        current_server,
                    )

                # Clean up a dead process with a lingering managed session before
                # creating the replacement session. A failed cleanup remains
                # non-fatal for compatibility with the existing guard behavior.
                try:
                    stop_success, stop_msg = await ssh_manager.stop_server(current_server)
                    if stop_success:
                        logger.info(
                            "Server %s stopped before auto-restart: %s",
                            server_id,
                            stop_msg,
                        )
                    else:
                        logger.warning(
                            "Server %s stop attempt before auto-restart: %s",
                            server_id,
                            stop_msg,
                        )
                except Exception as stop_error:
                    logger.warning(
                        "Error stopping server %s before auto-restart: %s",
                        server_id,
                        stop_error,
                    )

                restart_success, restart_msg = await ssh_manager.start_server(
                    current_server, progress_callback
                )
                return restart_success, restart_msg, current_server
        except OperationBusyError as exc:
            return None, str(exc), None

    async def monitor_server(self, server_id: int, ssh_manager, progress_callback=None):
        """
        Monitor a server and auto-restart on crash

        Args:
            server_id: ID of server to monitor
            ssh_manager: SSHManager instance to use for checks and restarts
            progress_callback: Optional callback for progress updates
        """
        dependencies = MonitorDependencies(
            logger, get_current_time, automatic_start_block_reason, restart_protection_window
        )
        return await _delegated_monitor_server(
            self, dependencies, server_id, ssh_manager, progress_callback
        )

    def start_monitoring(self, server_id: int, ssh_manager, progress_callback=None):
        """Start monitoring a server in the background"""
        if server_id in self.monitoring_tasks:
            logger.warning(f"Server {server_id} is already being monitored")
            return

        task = asyncio.create_task(self.monitor_server(server_id, ssh_manager, progress_callback))
        self.monitoring_tasks[server_id] = task
        logger.info(f"Started monitoring task for server {server_id}")

    def stop_monitoring(self, server_id: int):
        """Stop monitoring a server"""
        if server_id in self.monitoring_tasks:
            self.monitoring_tasks[server_id].cancel()
            del self.monitoring_tasks[server_id]
            logger.info(f"Stopped monitoring for server {server_id}")
        self.manual_stop_suppressed.discard(server_id)

    async def stop_all(self) -> None:
        """Cancel and await every monitoring task before shared resources close."""
        tasks = list(self.monitoring_tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.monitoring_tasks.clear()
        self.manual_stop_suppressed.clear()


# Global monitor instance
server_monitor = ServerMonitor()
