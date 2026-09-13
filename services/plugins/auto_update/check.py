"""Auto-update mixins looked up through the public service facade."""

from __future__ import annotations

import fnmatch
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import update as sql_update
from sqlmodel import col, select

from modules.models import ManagedPlugin, Server, User
from services.compat import LateBoundModule
from services.discord_notification_service import EVENT_PLUGIN_UPDATE
from services.plugins.auto_update.types import AutoUpdateServiceProtocol

logger = logging.getLogger("services.plugin_auto_update_service")
host = LateBoundModule("services.plugin_auto_update_service")


class AutoUpdateCheckMixin:
    async def _check_server(  # noqa: C901
        self: AutoUpdateServiceProtocol,
        server_id: int,
        force: bool = False,
        plugin_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        lock = host.maintenance_lock_service.get(
            server_id,
            operation="plugin_auto_update",
            wait=False,
            ttl=3600,
        )

        async with lock:
            run_label = "Plugin test update" if plugin_id is not None else "Plugin update check"
            await self._publish_status(
                server_id,
                state="running",
                phase="checking",
                message=f"Loading {run_label.lower()} configuration",
                current=0,
                total=0,
                log=f"{run_label} started",
            )
            async with host.async_session_maker() as db:
                from services.plugins.diagnostic_policy import has_diagnostic_blocker

                if await has_diagnostic_blocker(server_id, db):
                    return {
                        "success": False,
                        "message": "Plugin diagnostic quarantine requires attention",
                    }
                server = await db.get(Server, server_id)
                if not server:
                    await self._publish_status(
                        server_id,
                        state="failed",
                        phase="failed",
                        message="Server not found",
                        log="Server not found",
                    )
                    return {"success": False, "message": "Server not found"}
                if not force and not server.enable_plugin_auto_update:
                    await self._publish_status(
                        server_id,
                        state="completed",
                        phase="disabled",
                        message="Plugin auto-update is disabled",
                        log="Scheduled check skipped because the server-level switch is disabled",
                    )
                    return {"success": False, "message": "Plugin auto-update is disabled"}
                user = await db.get(User, server.user_id)
                post_update_commands_enabled = bool(
                    getattr(server, "enable_plugin_post_update_commands", False)
                )
                post_update_command_ids = self._normalize_command_ids(
                    getattr(server, "plugin_post_update_command_ids", None)
                )
                item_filters = [col(ManagedPlugin.server_id) == server_id]
                if plugin_id is None:
                    item_filters.append(col(ManagedPlugin.auto_update_enabled).is_(True))
                else:
                    item_filters.append(col(ManagedPlugin.id) == plugin_id)
                result = await db.execute(select(ManagedPlugin).where(*item_filters))
                items = list(result.scalars().all())
                # A manual per-item test must not postpone the next scheduled
                # server-wide check.
                if plugin_id is None:
                    await db.execute(
                        sql_update(Server)
                        .where(col(Server.id) == server_id)
                        .values(last_plugin_update_check=host.get_current_time())
                    )
                await db.commit()

            from services.linux_runtime_service import detect_linux_runtime_profile

            linux_runtime_profile = await detect_linux_runtime_profile(server)

            if plugin_id is not None and not items:
                message = "Managed plugin not found"
                await self._publish_status(
                    server_id, state="failed", phase="failed", message=message, log=message
                )
                return {"success": False, "message": message}

            if not user:
                await self._publish_status(
                    server_id,
                    state="failed",
                    phase="failed",
                    message="Server owner not found",
                    log="Server owner not found",
                )
                return {"success": False, "message": "Server owner not found"}

            candidates: List[Tuple[ManagedPlugin, Dict[str, Any]]] = []
            resolve_failures: List[Tuple[ManagedPlugin, str]] = []
            await self._publish_status(
                server_id,
                phase="checking_releases",
                message=f"Checking {len(items)} selected plugin(s)",
                current=0,
                total=len(items),
                log=f"Found {len(items)} selected plugin(s)",
            )
            for item_index, item in enumerate(items, start=1):
                await self._publish_status(
                    server_id,
                    phase="checking_releases",
                    message=f"Requesting latest release for {item.display_name}",
                    current=item_index - 1,
                    total=len(items),
                    log=f"Requesting release metadata: {item.display_name}",
                )
                try:
                    if item.framework_key == "metamod":
                        ok, latest, error = await self._latest_metamod(server)
                    else:
                        ok, latest, error = await self._latest_github_release(
                            item,
                            user,
                            linux_runtime_profile,
                        )
                except Exception as exc:
                    logger.exception(
                        "Failed to resolve latest release for managed plugin %s", item.id
                    )
                    ok, latest, error = False, None, str(exc)
                same_release = bool(
                    ok
                    and latest
                    and (
                        item.installed_release_id == latest["release_id"]
                        or item.installed_version == latest["version"]
                    )
                )
                selected_asset_name = str(latest["asset"].get("name") or "") if latest else ""
                same_asset = bool(
                    not item.installed_asset_name
                    or item.installed_asset_name == selected_asset_name
                )
                if not item.installed_asset_name and item.asset_glob:
                    same_asset = fnmatch.fnmatchcase(selected_asset_name, item.asset_glob)
                is_current = same_release and same_asset
                async with host.async_session_maker() as db:
                    saved = await db.get(ManagedPlugin, item.id)
                    if saved:
                        saved.last_check_at = host.get_current_time()
                        saved.latest_version = latest["version"] if latest else None
                        if not ok:
                            saved.last_status = "failed"
                            saved.last_error = error
                        elif is_current:
                            saved.last_status = "up_to_date"
                            saved.last_error = None
                        db.add(saved)
                        await db.commit()
                if not ok or not latest:
                    resolve_failures.append((item, error))
                    await self._publish_status(
                        server_id,
                        current=item_index,
                        log=f"Release check failed for {item.display_name}: {error}",
                    )
                elif not is_current:
                    candidates.append((item, latest))
                    await self._publish_status(
                        server_id,
                        current=item_index,
                        log=f"Update available for {item.display_name}: {item.installed_version} -> {latest['version']}",
                    )
                else:
                    await self._publish_status(
                        server_id, current=item_index, log=f"{item.display_name} is up to date"
                    )

            if not candidates:
                if resolve_failures:
                    host.discord_notification_service.queue_notify(
                        server,
                        EVENT_PLUGIN_UPDATE,
                        "plugin_auto_update",
                        False,
                        "One or more plugin update checks failed; no files were changed.",
                        title="Plugin automatic update check failed",
                        details={
                            "Failures": "\n".join(
                                f"{item.display_name}: {error}" for item, error in resolve_failures
                            )
                        },
                    )
                terminal_success = not resolve_failures
                terminal_message = (
                    "No plugin updates available"
                    if terminal_success
                    else "Plugin update checks failed"
                )
                await self._publish_status(
                    server_id,
                    state="completed" if terminal_success else "failed",
                    phase="completed" if terminal_success else "failed",
                    message=terminal_message,
                    current=len(items),
                    total=len(items),
                    log=terminal_message,
                )
                return {
                    "success": not resolve_failures,
                    "message": terminal_message,
                    "failures": [
                        f"{item.display_name}: {error}" for item, error in resolve_failures
                    ],
                }

            candidates.sort(
                key=lambda pair: {"metamod": 0, "counterstrikesharp": 1}.get(
                    pair[0].framework_key or "", 2
                )
            )
            # Restart is a batch-level policy for every managed item (ordinary
            # GitHub/market plugins and frameworks).  Multiple selected items
            # therefore result in one state check and, at most, one restart.
            restart_candidates = [item for item, _ in candidates if item.restart_after_update]
            status_check_ok = True
            was_running = False
            status_check_message = "Restart not requested"
            if restart_candidates:
                await self._publish_status(
                    server_id,
                    phase="checking_server",
                    message="Checking server state for batch restart policy",
                    current=0,
                    total=len(candidates),
                    log="Checking whether the server is currently running",
                )
                status_check_ok, server_state = await host._ssh_manager().get_server_status(server)
                was_running = status_check_ok and server_state == "running"
                status_check_message = (
                    server_state if status_check_ok else "Could not determine server state"
                )

            targets = "\n".join(
                f"{item.display_name}: {item.installed_version} → {latest['version']}"
                for item, latest in candidates
            )
            host.discord_notification_service.queue_notify(
                server,
                EVENT_PLUGIN_UPDATE,
                "plugin_auto_update",
                True,
                "Plugin auto-update is starting. Restart settings will be applied once after the batch.",
                title="Plugin automatic update started",
                details={
                    "Updates": targets,
                    "Backup Before Update": ", ".join(
                        item.display_name for item, _ in candidates if item.backup_before_update
                    )
                    or "Not requested",
                    "Auto Restart": ", ".join(item.display_name for item in restart_candidates)
                    or "Not requested",
                },
                state="in_progress",
            )

            backup_items = [
                (item, latest) for item, latest in candidates if item.backup_before_update
            ]
            backup_success = True
            backup_message = "Not requested"
            backup_blocked_ids = set()
            if backup_items:
                await self._publish_status(
                    server_id,
                    phase="backup",
                    message="Creating local backup for selected plugins",
                    current=0,
                    total=len(candidates),
                    log="Backup requested by: "
                    + ", ".join(item.display_name for item, _ in backup_items),
                )
                backup_success, backup_message = await host._ssh_manager().backup_plugins(server)
            if backup_items and not backup_success:
                message = (
                    f"Plugin backup failed; plugins requiring backup were skipped: {backup_message}"
                )
                backup_blocked_ids = {item.id for item, _ in backup_items}
                async with host.async_session_maker() as db:
                    for item, _ in backup_items:
                        saved = await db.get(ManagedPlugin, item.id)
                        if saved:
                            saved.last_status = "failed"
                            saved.last_error = message
                            db.add(saved)
                    await db.commit()
                await self._publish_status(
                    server_id,
                    phase="backup_failed",
                    message=message,
                    current=0,
                    total=len(candidates),
                    log=message,
                )

            results: List[Dict[str, Any]] = []
            if backup_items and backup_success:
                update_start_message = "Selected-plugin backup completed; starting plugin updates"
                update_start_log = f"Backup completed: {backup_message}"
            elif backup_items:
                update_start_message = "Continuing plugins that do not require backup"
                update_start_log = (
                    "Backup-required plugins were skipped; continuing unprotected plugins"
                )
            else:
                update_start_message = "No plugin backups requested; starting plugin updates"
                update_start_log = "Backup skipped because no selected plugin enabled it"
            await self._publish_status(
                server_id, phase="updating", message=update_start_message, log=update_start_log
            )
            for update_index, (item, latest) in enumerate(candidates, start=1):
                if item.id in backup_blocked_ids:
                    message = f"Skipped because the requested backup failed: {backup_message}"
                    results.append(
                        {
                            "name": item.display_name,
                            "success": False,
                            "message": message,
                            "version": latest["version"],
                            "restart_after_update": item.restart_after_update,
                            "source_type": item.source_type,
                        }
                    )
                    await self._publish_status(
                        server_id,
                        current=update_index,
                        total=len(candidates),
                        log=f"Skipped {item.display_name}: requested backup failed",
                    )
                    continue
                await self._publish_status(
                    server_id,
                    phase="updating",
                    message=f"Updating {item.display_name}",
                    current=update_index - 1,
                    total=len(candidates),
                    log=f"Updating {item.display_name} to {latest['version']}",
                )
                try:
                    success, message = await self._install_item(server, user, item, latest)
                except Exception as exc:
                    logger.exception("Managed plugin update failed for item %s", item.id)
                    success, message = False, str(exc)
                async with host.async_session_maker() as db:
                    saved = await db.get(ManagedPlugin, item.id)
                    if saved:
                        saved.last_status = "success" if success else "failed"
                        saved.last_error = None if success else message
                        if success:
                            saved.installed_release_id = latest["release_id"]
                            saved.installed_version = latest["version"]
                            saved.installed_asset_name = latest["asset"].get("name")
                            saved.asset_glob = host.derive_asset_glob(
                                latest["asset"].get("name"), latest["version"]
                            )
                            saved.last_update_at = host.get_current_time()
                        db.add(saved)
                        await db.commit()
                results.append(
                    {
                        "name": item.display_name,
                        "success": success,
                        "message": message,
                        "version": latest["version"],
                        "asset_name": latest["asset"].get("name"),
                        "restart_after_update": item.restart_after_update,
                        "source_type": item.source_type,
                    }
                )
                await self._publish_status(
                    server_id,
                    current=update_index,
                    log=f"{'Completed' if success else 'Failed'} {item.display_name}: {message}",
                )

            successful_restart_items = [
                result for result in results if result["success"] and result["restart_after_update"]
            ]
            restart_success = True
            restart_message = "Not requested"
            if successful_restart_items:
                if not status_check_ok:
                    restart_success = False
                    restart_message = "Automatic restart requested, but the pre-update server state could not be determined"
                elif not was_running:
                    restart_message = "Skipped because the server was stopped before the update"
                else:
                    await self._publish_status(
                        server_id,
                        phase="restarting",
                        message="Restarting server once after plugin update batch",
                        current=len(candidates),
                        total=len(candidates),
                        log="Batch restart policy triggered one server restart",
                    )
                    restart_manager = host._ssh_manager()
                    (
                        manager_ready,
                        preflight_message,
                    ) = await restart_manager.check_session_manager_available(server)
                    if not manager_ready:
                        restart_success = False
                        restart_message = (
                            f"Restart aborted before stopping: {preflight_message}. "
                            "The existing game session was left untouched."
                        )
                        await self._publish_status(server_id, log=restart_message)
                    else:
                        stop_success, stop_message = await restart_manager.stop_server(server)
                        await self._publish_status(server_id, log=f"Stop result: {stop_message}")
                        if not stop_success:
                            # Never issue start after a failed stop: that could
                            # create a second process in another managed session.
                            restart_success = False
                            restart_message = (
                                f"Restart failed while stopping server: {stop_message}"
                            )
                            await self._publish_status(server_id, log=restart_message)
                        else:
                            await host.asyncio.sleep(0.5)
                            start_success, start_message = await restart_manager.start_server(
                                server
                            )
                            restart_success = start_success
                            restart_message = (
                                start_message
                                if start_success
                                else f"Restart failed: {start_message}"
                            )
                            await self._publish_status(
                                server_id, log=f"Start result: {restart_message}"
                            )

            post_update_success = True
            post_update_message = "Not requested"
            post_update_results: List[Dict[str, Any]] = []
            successful_updates = [result for result in results if result["success"]]
            # Only after every selected plugin update has finished (and any batch
            # restart has been attempted). Commands run only when at least one
            # plugin was actually updated successfully.
            if post_update_commands_enabled and post_update_command_ids and successful_updates:
                await self._publish_status(
                    server_id,
                    phase="post_update_commands",
                    message="Running configured post-update quick commands",
                    current=len(candidates),
                    total=len(candidates),
                    log=(
                        "All plugin updates finished; executing "
                        f"{len(post_update_command_ids)} post-update quick command(s)"
                    ),
                )
                post_update = await self._execute_post_update_commands(
                    server, post_update_command_ids
                )
                post_update_success = bool(post_update.get("success"))
                post_update_message = post_update.get("message") or post_update_message
                post_update_results = list(post_update.get("results") or [])
            elif post_update_commands_enabled and post_update_command_ids:
                post_update_message = (
                    "Skipped because no plugin was updated successfully in this batch"
                )
                await self._publish_status(server_id, log=post_update_message)

            all_success = (
                all(result["success"] for result in results)
                and not resolve_failures
                and restart_success
                and post_update_success
            )
            summary_lines = [
                f"{'✓' if result['success'] else '✗'} {result['name']}: {result['version']}"
                + ("" if result["success"] else f" — {result['message']}")
                for result in results
            ]
            summary_lines.extend(
                f"✗ {item.display_name}: {error}" for item, error in resolve_failures
            )
            host.discord_notification_service.queue_notify(
                server,
                EVENT_PLUGIN_UPDATE,
                "plugin_auto_update",
                all_success,
                "Plugin update batch completed and the configured restart policy was applied once."
                if all_success
                else "One or more plugin updates or the configured batch restart failed.",
                title="Plugin automatic update completed"
                if all_success
                else "Plugin automatic update failed",
                details={
                    "Results": "\n".join(summary_lines),
                    "Backup": backup_message,
                    "Restart": restart_message,
                    "Post-update Commands": (
                        post_update_message
                        if not post_update_results
                        else "\n".join(
                            ("OK" if item["success"] else "FAIL")
                            + " "
                            + item["name"]
                            + ": "
                            + item["message"]
                            for item in post_update_results
                        )
                    ),
                },
            )
            terminal_message = (
                "Plugin update batch completed"
                if all_success
                else "Plugin update batch completed with failures"
            )
            await self._publish_status(
                server_id,
                state="completed" if all_success else "failed",
                phase="completed" if all_success else "failed",
                message=terminal_message,
                current=len(candidates),
                total=len(candidates),
                log=(
                    f"{terminal_message}. Restart: {restart_message}. "
                    f"Post-update commands: {post_update_message}"
                ),
            )
            return {
                "success": all_success,
                "message": terminal_message,
                "results": results,
                "restart": {
                    "success": restart_success,
                    "message": restart_message,
                    "previous_state": status_check_message,
                },
                "post_update_commands": {
                    "success": post_update_success,
                    "message": post_update_message,
                    "results": post_update_results,
                },
            }
