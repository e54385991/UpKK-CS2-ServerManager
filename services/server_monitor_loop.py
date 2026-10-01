"""Focused helpers; mutable state remains owned by the original controller."""

from __future__ import annotations

from typing import TYPE_CHECKING

from services.compat import LateBoundModule

if TYPE_CHECKING:
    from .server_monitor import ServerMonitor

host = LateBoundModule("services.server_monitor")


async def monitor_server(self: ServerMonitor, server_id: int, ssh_manager, progress_callback=None):
    """
    Monitor a server and auto-restart on crash

    Args:
        server_id: ID of server to monitor
        ssh_manager: SSHManager instance to use for checks and restarts
        progress_callback: Optional callback for progress updates
    """
    from modules.database import async_session_maker
    from modules.models import Server, ServerStatus
    from services.redis_manager import redis_manager

    host.logger.info(f"Starting panel-based monitoring for server {server_id}")

    # Log monitoring start to Redis
    try:
        success = await redis_manager.append_monitoring_log(
            server_id=server_id,
            event_type="monitoring_start",
            status="info",
            message="Panel monitoring started",
        )
        host.logger.info(f"Logged monitoring_start to Redis: success={success}")
    except Exception as e:
        host.logger.error(f"Failed to log monitoring_start to Redis: {e}")

    try:
        # Initialize check interval with default value
        check_interval = 60

        while True:
            # Get fresh server data from database - close session quickly to avoid pool exhaustion
            async with async_session_maker() as db:
                server = await db.get(Server, server_id)

                if not server:
                    host.logger.warning(
                        f"Server {server_id} not found in database, stopping monitoring"
                    )
                    try:
                        await redis_manager.append_monitoring_log(
                            server_id=server_id,
                            event_type="monitoring_stop",
                            status="warning",
                            message="Monitoring stopped: server not found in database",
                        )
                    except Exception as e:
                        host.logger.error(f"Failed to log monitoring_stop to Redis: {e}")
                    break

                # Check if monitoring is still enabled
                if not server.enable_panel_monitoring:
                    host.logger.info(f"Monitoring disabled for server {server_id}, stopping")
                    try:
                        await redis_manager.append_monitoring_log(
                            server_id=server_id,
                            event_type="monitoring_stop",
                            status="info",
                            message="Panel monitoring disabled by user",
                        )
                    except Exception as e:
                        host.logger.error(f"Failed to log monitoring_stop to Redis: {e}")
                    break

                # Determine check interval based on monitoring type
                if server.enable_a2s_monitoring:
                    # Use separate A2S check interval
                    check_interval = server.a2s_check_interval_seconds or 60
                else:
                    # Use general monitoring interval
                    check_interval = server.monitor_interval_seconds or 60

            # DB session closed here - perform network/SSH operations without holding DB connection

            block_reason = host.automatic_start_block_reason(server)
            if block_reason:
                self.a2s_failure_count.pop(server_id, None)
                if server_id not in self.manual_stop_suppressed:
                    host.logger.info(
                        "Pausing monitoring guard for server %s: %s", server_id, block_reason
                    )
                    self.manual_stop_suppressed.add(server_id)
                await host.asyncio.sleep(check_interval)
                continue
            if server_id in self.manual_stop_suppressed:
                self.manual_stop_suppressed.discard(server_id)
                host.logger.info("Resuming monitoring guard for server %s", server_id)

            # Determine if server is down based on monitoring type
            is_down = False
            check_status = "success"
            check_message = "Server is running"
            event_type = "status_check"

            # A2S monitoring (preferred if enabled)
            if server.enable_a2s_monitoring:
                from services.a2s_query import a2s_service

                # Use configured A2S host/port or fall back to server host/game_port
                query_host = server.a2s_query_host or server.host
                query_port = server.a2s_query_port or server.game_port

                host.logger.debug(
                    f"Performing A2S query for server {server_id} at {query_host}:{query_port}"
                )

                event_type = "a2s_check"

                try:
                    # Perform A2S health check
                    a2s_success = await a2s_service.check_server_health(
                        query_host, query_port, timeout=5.0
                    )

                    if a2s_success:
                        # Reset failure counter on success
                        if server_id in self.a2s_failure_count:
                            self.a2s_failure_count[server_id] = 0

                        check_status = "success"
                        check_message = f"A2S query successful at {query_host}:{query_port}"
                        host.logger.debug(f"Server {server_id} A2S query successful")
                    else:
                        # Increment failure counter
                        if server_id not in self.a2s_failure_count:
                            self.a2s_failure_count[server_id] = 0
                        self.a2s_failure_count[server_id] += 1

                        failure_count = self.a2s_failure_count[server_id]
                        threshold = server.a2s_failure_threshold or 3

                        check_status = "warning"
                        check_message = f"A2S query failed at {query_host}:{query_port} ({failure_count}/{threshold} failures)"
                        host.logger.warning(
                            f"Server {server_id} A2S query failed: {failure_count}/{threshold}"
                        )

                        # Mark as down if threshold exceeded
                        if failure_count >= threshold:
                            is_down = True
                            check_status = "failed"
                            check_message = f"A2S query failed {failure_count} consecutive times (threshold: {threshold})"
                except Exception as e:
                    is_down = True
                    check_status = "failed"
                    check_message = f"A2S query error: {str(e)}"
                    host.logger.error(f"Server {server_id} A2S query exception: {e}")

            # Process-based monitoring (if A2S not enabled)
            elif server.enable_panel_monitoring:
                # Check server status via SSH
                try:
                    success, status_msg = await ssh_manager.get_server_status(server)

                    host.logger.debug(
                        f"Server {server_id} status check: success={success}, status={status_msg}"
                    )

                    if not success:
                        is_down = True
                        check_status = "failed"
                        check_message = f"Status check failed: {status_msg}"
                        host.logger.warning(f"Server {server_id} status check failed: {status_msg}")
                    elif status_msg == "stopped":
                        is_down = True
                        check_status = "warning"
                        check_message = "Server process not found"
                        host.logger.warning(f"Server {server_id} process not found")
                    else:
                        check_status = "success"
                        check_message = f"Server is {status_msg}"
                except Exception as e:
                    is_down = True
                    check_status = "failed"
                    check_message = f"SSH status check error: {str(e)}"
                    host.logger.error(f"Server {server_id} SSH status check exception: {e}")

            # Log status check to Redis
            try:
                log_success = await redis_manager.append_monitoring_log(
                    server_id=server_id,
                    event_type=event_type,
                    status=check_status,
                    message=check_message,
                )
                host.logger.info(
                    f"Status check logged to Redis: server={server_id}, type={event_type}, status={check_status}, success={log_success}"
                )
            except Exception as e:
                host.logger.error(
                    f"Exception logging status check to Redis: server={server_id}, error={e}"
                )

            if is_down and server.auto_restart_on_crash:
                # Check if we can restart (respecting restart limits)
                can_restart, reason = self.can_restart(
                    server_id,
                    window=host.restart_protection_window(server),
                )

                if can_restart:
                    # Determine restart trigger source
                    if server.enable_a2s_monitoring:
                        restart_trigger = f"A2S monitoring ({self.a2s_failure_count.get(server_id, 0)} consecutive failures)"
                    else:
                        restart_trigger = "panel monitoring (process check)"

                    try:
                        (
                            restart_success,
                            restart_msg,
                            current_server,
                        ) = await self._perform_guarded_restart(
                            server_id,
                            ssh_manager,
                            progress_callback,
                        )
                        if restart_success is None:
                            host.logger.info(
                                "Skipping auto-restart for server %s: %s",
                                server_id,
                                restart_msg,
                            )
                            await host.asyncio.sleep(check_interval)
                            continue
                        server = current_server

                        host.logger.info(
                            "Auto-restarting server %s via %s",
                            server_id,
                            restart_trigger,
                        )
                        try:
                            await redis_manager.append_monitoring_log(
                                server_id=server_id,
                                event_type="auto_restart",
                                status="info",
                                message=f"Auto-restart triggered by {restart_trigger}",
                            )
                        except Exception as e:
                            host.logger.error(f"Failed to log restart trigger to Redis: {e}")

                        async with async_session_maker() as db:
                            server_to_update = await db.get(Server, server_id)
                            if server_to_update:
                                if restart_success:
                                    host.logger.info(
                                        f"Successfully auto-restarted server {server_id}"
                                    )
                                    server_to_update.status = ServerStatus.RUNNING
                                else:
                                    host.logger.error(
                                        f"Failed to auto-restart server {server_id}: {restart_msg}"
                                    )
                                    server_to_update.status = ServerStatus.ERROR
                                await db.commit()

                        if restart_success:
                            self.queue_restart_notification(
                                server,
                                success=True,
                                title="Auto-restart completed",
                                message=restart_msg or "Auto-restart completed successfully",
                                trigger=restart_trigger,
                                details={
                                    "Health Check": check_message,
                                },
                            )
                            try:
                                await redis_manager.append_monitoring_log(
                                    server_id=server_id,
                                    event_type="auto_restart",
                                    status="success",
                                    message="Auto-restart completed successfully",
                                )
                            except Exception as e:
                                host.logger.error(f"Failed to log restart success to Redis: {e}")
                        else:
                            self.queue_restart_notification(
                                server,
                                success=False,
                                title="Auto-restart failed",
                                message=restart_msg or "Auto-restart failed",
                                trigger=restart_trigger,
                                details={
                                    "Health Check": check_message,
                                },
                            )
                            try:
                                await redis_manager.append_monitoring_log(
                                    server_id=server_id,
                                    event_type="auto_restart",
                                    status="failed",
                                    message=f"Auto-restart failed: {restart_msg}",
                                )
                            except Exception as e:
                                host.logger.error(f"Failed to log restart failure to Redis: {e}")
                    except Exception as e:
                        host.logger.error(
                            f"Exception during auto-restart of server {server_id}: {e}"
                        )
                        self.queue_restart_notification(
                            server,
                            success=False,
                            title="Auto-restart failed",
                            message=f"Auto-restart error: {str(e)}",
                            trigger=restart_trigger,
                            details={
                                "Health Check": check_message,
                            },
                        )

                        async with async_session_maker() as db:
                            server_to_update = await db.get(Server, server_id)
                            if server_to_update:
                                server_to_update.status = ServerStatus.ERROR
                                await db.commit()

                        try:
                            await redis_manager.append_monitoring_log(
                                server_id=server_id,
                                event_type="auto_restart",
                                status="failed",
                                message=f"Auto-restart error: {str(e)}",
                            )
                        except Exception as redis_e:
                            host.logger.error(f"Failed to log restart error to Redis: {redis_e}")
                else:
                    host.logger.warning(f"Cannot auto-restart server {server_id}: {reason}")
                    self._queue_restart_block_notification(
                        server,
                        message=reason,
                        check_message=check_message,
                    )

                    async with async_session_maker() as db:
                        server_to_update = await db.get(Server, server_id)
                        if server_to_update:
                            server_to_update.status = ServerStatus.ERROR
                            await db.commit()

                    try:
                        await redis_manager.append_monitoring_log(
                            server_id=server_id,
                            event_type="auto_restart",
                            status="warning",
                            message=f"Auto-restart blocked: {reason}",
                        )
                    except Exception as e:
                        host.logger.error(f"Failed to log blocked restart to Redis: {e}")
            elif is_down:
                host.logger.info(f"Server {server_id} is down but auto-restart is disabled")
                self.queue_restart_notification(
                    server,
                    success=False,
                    title="Server down, auto-restart disabled",
                    message=check_message,
                    trigger="monitoring detected server down",
                    details={
                        "Auto Restart": "Disabled",
                        "Monitor": "A2S" if server.enable_a2s_monitoring else "Panel process check",
                    },
                )
                async with async_session_maker() as db:
                    server_to_update = await db.get(Server, server_id)
                    if server_to_update:
                        server_to_update.status = ServerStatus.STOPPED
                        await db.commit()
            else:
                async with async_session_maker() as db:
                    server_to_update = await db.get(Server, server_id)
                    if server_to_update:
                        if server_to_update.status != ServerStatus.RUNNING:
                            server_to_update.status = ServerStatus.RUNNING
                        server_to_update.last_status_check = host.get_current_time()
                        await db.commit()

            await host.asyncio.sleep(check_interval)

    except host.asyncio.CancelledError:
        host.logger.info(f"Monitoring cancelled for server {server_id}")
        raise
    except Exception as e:
        host.logger.error(f"Error monitoring server {server_id}: {e}", exc_info=True)
    finally:
        self.manual_stop_suppressed.discard(server_id)
