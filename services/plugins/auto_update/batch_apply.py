"""Lifecycle phases sharing the original domain facade and explicit inputs."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from modules.models import ManagedPlugin, Server, User
from services.compat import LateBoundModule
from services.plugins.auto_update.types import AutoUpdateServiceProtocol

logger = logging.getLogger("services.plugin_auto_update_service")

host = LateBoundModule("services.plugin_auto_update_service")


async def _backup_update_candidates(
    self: AutoUpdateServiceProtocol,
    server_id: int,
    server: Server,
    candidates: List[Tuple[ManagedPlugin, Dict[str, Any]]],
) -> tuple[List[Tuple[ManagedPlugin, Dict[str, Any]]], bool, str, set[int]]:
    backup_items = [(item, latest) for item, latest in candidates if item.backup_before_update]
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
            log="Backup requested by: " + ", ".join(item.display_name for item, _ in backup_items),
        )
        backup_success, backup_message = await host._ssh_manager().backup_plugins(server)
    if backup_items and not backup_success:
        message = f"Plugin backup failed; plugins requiring backup were skipped: {backup_message}"
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

    return backup_items, backup_success, backup_message, backup_blocked_ids


async def _apply_update_candidates(
    self: AutoUpdateServiceProtocol,
    server_id: int,
    server: Server,
    user: User,
    candidates: List[Tuple[ManagedPlugin, Dict[str, Any]]],
    backup_items: List[Tuple[ManagedPlugin, Dict[str, Any]]],
    backup_success: bool,
    backup_message: str,
    backup_blocked_ids: set[int],
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    if backup_items and backup_success:
        update_start_message = "Selected-plugin backup completed; starting plugin updates"
        update_start_log = f"Backup completed: {backup_message}"
    elif backup_items:
        update_start_message = "Continuing plugins that do not require backup"
        update_start_log = "Backup-required plugins were skipped; continuing unprotected plugins"
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

    return results


async def _restart_after_batch(
    self: AutoUpdateServiceProtocol,
    server_id: int,
    server: Server,
    candidates: List[Tuple[ManagedPlugin, Dict[str, Any]]],
    results: List[Dict[str, Any]],
    status_check_ok: bool,
    was_running: bool,
) -> tuple[bool, str]:
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
                    restart_message = f"Restart failed while stopping server: {stop_message}"
                    await self._publish_status(server_id, log=restart_message)
                else:
                    await host.asyncio.sleep(0.5)
                    start_success, start_message = await restart_manager.start_server(server)
                    restart_success = start_success
                    restart_message = (
                        start_message if start_success else f"Restart failed: {start_message}"
                    )
                    await self._publish_status(server_id, log=f"Start result: {restart_message}")

    return restart_success, restart_message
