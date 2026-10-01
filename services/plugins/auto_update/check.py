"""Auto-update mixins looked up through the public service facade."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from services.compat import LateBoundModule
from services.discord_notification_service import EVENT_PLUGIN_UPDATE
from services.plugins.auto_update.types import AutoUpdateServiceProtocol

from .batch_apply import _apply_update_candidates as _apply_update_candidates
from .batch_apply import _backup_update_candidates as _backup_update_candidates
from .batch_apply import _restart_after_batch as _restart_after_batch
from .release_checks import _finish_empty_check as _finish_empty_check
from .release_checks import _load_update_configuration as _load_update_configuration
from .release_checks import _resolve_update_candidates as _resolve_update_candidates

logger = logging.getLogger("services.plugin_auto_update_service")
host = LateBoundModule("services.plugin_auto_update_service")


class AutoUpdateCheckMixin:
    async def _check_server(
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
            configuration = await _load_update_configuration(self, server_id, force, plugin_id)
            if isinstance(configuration, dict):
                return configuration
            server, user, post_update_commands_enabled, post_update_command_ids, items = (
                configuration
            )

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

            candidates, resolve_failures = await _resolve_update_candidates(
                self, server_id, server, user, items, linux_runtime_profile
            )

            if not candidates:
                return await _finish_empty_check(self, server_id, server, items, resolve_failures)

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

            (
                backup_items,
                backup_success,
                backup_message,
                backup_blocked_ids,
            ) = await _backup_update_candidates(self, server_id, server, candidates)

            results = await _apply_update_candidates(
                self,
                server_id,
                server,
                user,
                candidates,
                backup_items,
                backup_success,
                backup_message,
                backup_blocked_ids,
            )

            restart_success, restart_message = await _restart_after_batch(
                self, server_id, server, candidates, results, status_check_ok, was_running
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
