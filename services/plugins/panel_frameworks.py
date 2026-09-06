"""Route framework marketplace entries through the panel-native installers.

Metamod and CounterStrikeSharp have dedicated installers (version tracking,
runtime fixes, ``core.json`` seeding). A marketplace listing that *is* one of
those frameworks must use them instead of a generic archive extract.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from modules import MarketPlugin, Server, User
from services.plugins.progress import emit_plan_progress

ProgressCallback = Callable[..., Awaitable[None]]
logger = logging.getLogger(__name__)

GITHUB_REPOSITORY_PATTERN = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)

_PANEL_FRAMEWORK_REPOSITORIES = {
    ("alliedmodders", "metamod-source"): "metamod",
    ("roflmuffin", "counterstrikesharp"): "counterstrikesharp",
}


def panel_framework_key(plugin: MarketPlugin) -> str | None:
    """Identify frameworks that must use the panel's dedicated installers."""
    match = GITHUB_REPOSITORY_PATTERN.fullmatch((plugin.github_url or "").strip().rstrip("/"))
    if match:
        groups = match.groups()
        repository_key = (str(groups[0]).casefold(), str(groups[1]).casefold())
        if repository_key in _PANEL_FRAMEWORK_REPOSITORIES:
            return _PANEL_FRAMEWORK_REPOSITORIES[repository_key]
    normalized_title = re.sub(r"[^a-z0-9]+", "", (plugin.title or "").casefold())
    if normalized_title in {"metamod", "metamodsource"}:
        return "metamod"
    if normalized_title == "counterstrikesharp":
        return "counterstrikesharp"
    return None


async def install_panel_framework(
    db: AsyncSession,
    plugin: MarketPlugin,
    server: Server,
    user: User,
    framework_key: str,
    progress: ProgressCallback | None,
) -> dict[str, Any]:
    """Route framework market entries through the panel-native installer."""
    from services.plugin_auto_update_service import record_framework_installation
    from services.ssh_manager import SSHManager

    await emit_plan_progress(
        progress,
        f"Using the panel-native installer for {plugin.title}",
        step_id=f"plugin:{plugin.id}",
        step_status="running",
    )

    async def framework_progress(
        message: str,
        _kind: str = "status",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await emit_plan_progress(
            progress,
            message,
            step_id=f"plugin:{plugin.id}",
            step_status="running",
            metadata=metadata,
        )

    manager = SSHManager()
    if framework_key == "metamod":
        success, message = await manager.install_metamod(server, framework_progress)
        installed_frameworks = ("metamod",)
    else:
        success, message = await manager.install_counterstrikesharp(server, framework_progress)
        installed_frameworks = ("metamod", "counterstrikesharp")
    if not success:
        return {"success": False, "plugin_id": plugin.id, "message": message}

    tracking_failed = False
    for installed_framework in installed_frameworks:
        try:
            await record_framework_installation(server, user, installed_framework)
        except Exception as exc:
            tracking_failed = True
            logger.warning(
                "Framework %s installed on server %s but tracking refresh failed: %s",
                installed_framework,
                server.id,
                exc,
            )
    plugin.download_count += 1
    plugin.install_count += 1
    db.add(plugin)
    await db.commit()
    result: dict[str, Any] = {
        "success": True,
        "plugin_id": plugin.id,
        "message": message,
        "installation_method": "panel_native",
        "framework": framework_key,
        "restart_required": True,
        "next_step": (
            "Restart (or start) the server and wait for startup before locating generated configs."
        ),
    }
    if tracking_failed:
        result["tracking_warning"] = (
            "The framework was installed, but panel version tracking could not be refreshed."
        )
    return result
