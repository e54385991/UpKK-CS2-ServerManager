"""
Auto-Update Service for CS2 Servers
Periodically checks server versions against Steam API and triggers updates when needed
"""

import asyncio
import logging
import math
from typing import Optional, Set, Tuple

from modules.utils import get_current_time
from services.discord_notification_service import EVENT_AUTO_UPDATE, discord_notification_service
from services.maintenance_lock import maintenance_lock_service
from services.ssh_manager import SSHManager
from services.steam_api_service import steam_api_service
from services.steam_inf_service import steam_inf_service

logger = logging.getLogger(__name__)


class AutoUpdateService:
    """Background service to check and update CS2 servers automatically"""

    VERSION_VERIFICATION_TIMEOUT_SECONDS = 5 * 60
    VERSION_VERIFICATION_POLL_INTERVAL_SECONDS = 30

    def __init__(self):
        self.check_interval = 60  # Check every minute (configurable, supports debugging)
        self.task: Optional[asyncio.Task] = None
        self.running = False
        self.updating_servers: Set[int] = set()  # Track servers currently being updated

    async def start(self):
        """Start the background auto-update task"""
        if self.task is None or self.task.done():
            self.running = True
            self.task = asyncio.create_task(self._update_loop())
            logger.info("Auto-update service started")

    async def stop(self):
        """Stop the background auto-update task"""
        self.running = False
        if self.task and not self.task.done():
            self.task.cancel()
        if self.task:
            await asyncio.gather(self.task, return_exceptions=True)
        self.task = None
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
                        f"Skipping server {server.id} ({server.name}) - update already in progress"
                    )
                    continue

                # Skip servers that are marked as down due to SSH failures
                if server.should_skip_background_checks():
                    logger.info(
                        f"Skipping auto-update check for server {server.id} ({server.name}) - marked as SSH down for 3+ days"
                    )
                    continue

                # Check if we should check this server based on its configured interval
                interval_hours = server.update_check_interval_hours or 1
                if not steam_api_service.should_check_version(
                    server.last_update_check, interval_hours
                ):
                    logger.debug(
                        f"Skipping server {server.id} ({server.name}) - "
                        f"checked recently (interval: {interval_hours}h)"
                    )
                    continue

                await self._check_and_update_server(server)

        except Exception as e:
            logger.error(f"Error checking servers for updates: {e}")

    async def _check_and_update_server(self, server):
        """Check a single server and run a long update outside the check timeout."""
        try:

            async def _do_check():
                from sqlalchemy import update as sql_update

                from modules.database import async_session_maker
                from modules.models import Server

                async with async_session_maker() as db:
                    await db.execute(
                        sql_update(Server)
                        .where(Server.id == server.id)
                        .values(last_update_check=get_current_time())
                    )
                    await db.commit()

                success, version = await steam_inf_service.get_version_from_steam_inf(server)
                current_version = version if success and version else server.current_game_version
                version_source = "steam.inf" if success and version else "database/A2S"
                if not current_version:
                    logger.warning("No version available for server %s", server.id)
                    return None
                success, result = await steam_api_service.check_version(current_version)
                if not success:
                    logger.warning(
                        "Steam version check failed for server %s: %s",
                        server.id,
                        result.get("error"),
                    )
                    return None
                if result.get("up_to_date", True):
                    return None
                return current_version, result.get("required_version"), version_source

            update_info = await asyncio.wait_for(_do_check(), timeout=60)
            if update_info:
                await self._trigger_server_update(
                    server,
                    current_version=update_info[0],
                    required_version=update_info[1],
                    version_source=update_info[2],
                )
        except asyncio.TimeoutError:
            logger.warning("Timeout checking server %s; update was not started", server.id)
        except Exception as e:
            logger.error(f"Error checking/updating server {server.id}: {e}")

    async def _wait_for_updated_version(
        self,
        server,
        required_version: Optional[str],
        log_progress,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Poll fresh steam.inf reads until the target version is observed."""
        timeout = max(0, self.VERSION_VERIFICATION_TIMEOUT_SECONDS)
        interval = max(0, self.VERSION_VERIFICATION_POLL_INTERVAL_SECONDS)
        max_attempts = math.ceil(timeout / interval) + 1 if timeout and interval else 1
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout if timeout else None

        observed_version = None
        target_version = required_version
        latest_required_version = None
        checked_versions = {}

        for attempt in range(1, max_attempts + 1):
            try:
                if deadline is None:
                    verified_read, observed = await steam_inf_service.refresh_version_cache(server)
                else:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    verified_read, observed = await asyncio.wait_for(
                        steam_inf_service.refresh_version_cache(server),
                        timeout=remaining,
                    )
            except asyncio.TimeoutError:
                await log_progress("steam.inf verification window expired during a version read")
                break

            if verified_read and observed:
                observed_version = observed
                if self._versions_match(observed_version, target_version):
                    return True, observed_version, latest_required_version

                # A new Valve release can appear while SteamCMD is running.
                # Accept a different version only if Steam confirms that it is
                # currently up to date. Cache successful checks for unchanged
                # versions to avoid repeating the same API request every poll.
                final_check = checked_versions.get(observed_version)
                if final_check is None:
                    try:
                        if deadline is None:
                            check_success, candidate = await steam_api_service.check_version(
                                observed_version
                            )
                        else:
                            remaining = deadline - loop.time()
                            if remaining <= 0:
                                break
                            check_success, candidate = await asyncio.wait_for(
                                steam_api_service.check_version(observed_version),
                                timeout=remaining,
                            )
                    except asyncio.TimeoutError:
                        await log_progress(
                            "Version verification window expired during the Steam API check"
                        )
                        break
                    if check_success:
                        final_check = candidate
                        checked_versions[observed_version] = candidate

                if final_check:
                    if final_check.get("required_version"):
                        latest_required_version = final_check.get("required_version")
                        target_version = latest_required_version
                        if self._versions_match(observed_version, target_version):
                            return True, observed_version, latest_required_version
                    if final_check.get("up_to_date", False):
                        return True, observed_version, latest_required_version

                await log_progress(
                    f"steam.inf verification attempt {attempt}/{max_attempts} "
                    f"observed {observed_version}, required {target_version or 'current Steam version'}"
                )
            else:
                await log_progress(
                    f"steam.inf verification attempt {attempt}/{max_attempts} could not read a version"
                )

            if attempt < max_attempts:
                if deadline is None:
                    break
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(interval, remaining))

        return False, observed_version, latest_required_version

    @staticmethod
    def _versions_match(observed_version: Optional[str], required_version: Optional[str]) -> bool:
        """Compare dotted steam.inf versions with Steam's numeric fallback format."""
        if not observed_version or not required_version:
            return False
        observed_digits = "".join(
            character for character in observed_version if character.isdigit()
        )
        required_digits = "".join(
            character for character in required_version if character.isdigit()
        )
        return bool(observed_digits and observed_digits == required_digits)

    async def _trigger_server_update(
        self,
        server,
        current_version: Optional[str] = None,
        required_version: Optional[str] = None,
        version_source: Optional[str] = None,
    ):
        """Trigger update for a server and restart it"""
        from services.plugin_diagnostic_service import has_diagnostic_blocker

        if await has_diagnostic_blocker(server.id):
            logger.warning(
                "Skipping auto-update for server %s while plugin isolation requires attention",
                server.id,
            )
            return
        lock = maintenance_lock_service.get(server.id)

        # Try to acquire lock without blocking - if already locked, skip this update
        if await maintenance_lock_service.is_locked(server.id):
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
            output_messages = []
            notification_details = {
                "Current Version": current_version or "Unknown",
                "Required Version": required_version or "Unknown",
                "Version Source": version_source or "Unknown",
            }
            try:
                async with async_session_maker() as db:
                    log = DeploymentLog(
                        server_id=server.id, action="auto_update", status="in_progress"
                    )
                    db.add(log)
                    await db.commit()
                    await db.refresh(log)
                    log_id = log.id

                logger.info(f"Triggering auto-update for server {server.id} ({server.name})")

                discord_notification_service.queue_notify(
                    server,
                    EVENT_AUTO_UPDATE,
                    "auto_update",
                    True,
                    "A newer CS2 server version was detected. Automatic update is starting.",
                    title="Automatic update started",
                    details=notification_details,
                    state="in_progress",
                )

                # Create SSH manager
                ssh_manager = SSHManager()

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
                    server, progress_callback=log_progress
                )

                # A SteamCMD process can return a failure status after having
                # replaced the game files. Unless recovery itself failed,
                # reconcile that provisional result against fresh steam.inf
                # reads before sending a terminal failure notification.
                update_message_lower = update_message.lower()
                reconcile_steamcmd_failure = bool(
                    not update_success
                    and update_message_lower.startswith("steamcmd update failed:")
                    and "recovery start failed:" not in update_message_lower
                )

                if not update_success and not reconcile_steamcmd_failure:
                    error_msg = f"Update failed: {update_message}"
                    notification_details["Operation Result"] = update_message
                    logger.error(f"Update failed for server {server.id}: {update_message}")

                    # Update log as failed
                    async with async_session_maker() as db:
                        log_to_update = await db.get(DeploymentLog, log_id)
                        if log_to_update:
                            log_to_update.status = "failed"
                            log_to_update.error_message = error_msg
                            log_to_update.output = (
                                "\n".join(output_messages) if output_messages else None
                            )
                            await db.commit()
                    discord_notification_service.queue_notify(
                        server,
                        EVENT_AUTO_UPDATE,
                        "auto_update",
                        False,
                        error_msg,
                        title="Automatic update failed",
                        details=notification_details,
                    )
                    return

                if reconcile_steamcmd_failure:
                    notification_details["Operation Result"] = update_message
                    await log_progress(
                        "SteamCMD reported a failure; keeping the update in progress "
                        "while steam.inf is verified for up to 5 minutes"
                    )

                # SteamCMD success is not enough. Bypass Redis and poll the
                # remote steam.inf for the required version. A readable but
                # stale file must keep polling rather than fail immediately.
                notification_details["Verification Window"] = "Up to 5 minutes"
                (
                    version_verified,
                    observed_version,
                    latest_required_version,
                ) = await self._wait_for_updated_version(
                    server,
                    required_version,
                    log_progress,
                )

                notification_details["Observed Version"] = observed_version or "Unavailable"
                notification_details["Operation Result"] = update_message
                if latest_required_version:
                    notification_details["Latest Required Version"] = latest_required_version
                if reconcile_steamcmd_failure and version_verified:
                    notification_details["SteamCMD Reconciliation"] = (
                        "Reported failure, but fresh steam.inf verification passed"
                    )

                if not version_verified:
                    expected_version = (
                        latest_required_version or required_version or "current Steam version"
                    )
                    error_msg = (
                        "Update verification failed: steam.inf could not be read"
                        if not observed_version
                        else f"Update verification failed: steam.inf reports {observed_version}, required {expected_version}"
                    )
                    await log_progress(error_msg)
                    async with async_session_maker() as db:
                        log_to_update = await db.get(DeploymentLog, log_id)
                        if log_to_update:
                            log_to_update.status = "failed"
                            log_to_update.error_message = error_msg
                            log_to_update.output = "\n".join(output_messages)
                            await db.commit()
                    discord_notification_service.queue_notify(
                        server,
                        EVENT_AUTO_UPDATE,
                        "auto_update",
                        False,
                        error_msg,
                        title="Automatic update failed",
                        details=notification_details,
                    )
                    return

                logger.info(f"Auto-update completed successfully for server {server.id}")
                await log_progress(
                    f"Auto-update verified successfully via steam.inf: {observed_version}"
                )

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

                completion_message = (
                    f"SteamCMD reported a failure, but fresh steam.inf verification confirmed version {observed_version}"
                    if reconcile_steamcmd_failure
                    else f"Auto-update completed and steam.inf verified version {observed_version}"
                )
                discord_notification_service.queue_notify(
                    server,
                    EVENT_AUTO_UPDATE,
                    "auto_update",
                    True,
                    completion_message,
                    title="Automatic update completed",
                    details=notification_details,
                )

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
                discord_notification_service.queue_notify(
                    server,
                    EVENT_AUTO_UPDATE,
                    "auto_update",
                    False,
                    error_msg,
                    title="Automatic update failed",
                    details=notification_details,
                )
            finally:
                # Remove server from updating set
                self.updating_servers.discard(server.id)


# Global instance
auto_update_service = AutoUpdateService()
