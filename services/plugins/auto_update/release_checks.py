"""Lifecycle phases sharing the original domain facade and explicit inputs."""

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


async def _load_update_configuration(self: AutoUpdateServiceProtocol, server_id: int, force: bool, plugin_id: int | None) -> tuple[Server, User | None, bool, list[int], list[ManagedPlugin]] | Dict[str, Any]:
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

    return server, user, post_update_commands_enabled, post_update_command_ids, items


async def _resolve_update_candidates(self: AutoUpdateServiceProtocol, server_id: int, server: Server, user: User, items: list[ManagedPlugin], linux_runtime_profile: Dict[str, Any]) -> tuple[List[Tuple[ManagedPlugin, Dict[str, Any]]], List[Tuple[ManagedPlugin, str]]]:
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

    return candidates, resolve_failures


async def _finish_empty_check(self: AutoUpdateServiceProtocol, server_id: int, server: Server, items: list[ManagedPlugin], resolve_failures: List[Tuple[ManagedPlugin, str]]) -> Dict[str, Any]:
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


