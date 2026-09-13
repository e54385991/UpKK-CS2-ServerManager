"""Automatic updates for tracked GitHub plugins and frameworks."""

from __future__ import annotations

import asyncio
import logging

from modules.database import async_session_maker
from modules.http_helper import http_helper
from modules.models import ManagedPlugin, Server, User
from modules.utils import get_current_time
from services.custom_command_service import execute_custom_commands, format_custom_command_log
from services.discord_notification_service import discord_notification_service
from services.maintenance_lock import maintenance_lock_service
from services.plugin_installation import install_github_plugin
from services.plugins.auto_update.apply import AutoUpdateApplyMixin
from services.plugins.auto_update.assets import AutoUpdateAssetsMixin
from services.plugins.auto_update.check import AutoUpdateCheckMixin
from services.plugins.auto_update.loop import AutoUpdateLoopMixin
from services.plugins.tracking import (
    canonical_repo_url,
    derive_asset_glob,
    upsert_managed_plugin,
)
from services.redis_manager import redis_manager

logger = logging.getLogger(__name__)
SSHManager = None

# Mixins resolve these names through LateBoundModule so tests can still patch
# the public module path after the check/apply/loop split.
_PATCHABLE = (
    asyncio,
    async_session_maker,
    discord_notification_service,
    execute_custom_commands,
    format_custom_command_log,
    get_current_time,
    http_helper,
    install_github_plugin,
    maintenance_lock_service,
    redis_manager,
)

logger = logging.getLogger(__name__)
SSHManager = None


def _ssh_manager():
    if SSHManager is not None:
        return SSHManager()
    from services.ssh_manager import SSHManager as Manager

    return Manager()


CONFIG_EXCLUSIONS = ["*.cfg", "*.conf", "*.ini", "*.json", "*.toml", "*.yaml", "*.yml"]
FRAMEWORKS = {
    "counterstrikesharp": {
        "name": "CounterStrikeSharp",
        "repo_url": "https://github.com/roflmuffin/CounterStrikeSharp",
        "asset_glob": "counterstrikesharp-with-runtime-linux*.zip",
    },
    "metamod": {
        "name": "Metamod:Source",
        "repo_url": "https://github.com/alliedmodders/metamod-source",
        "asset_glob": "mmsource-*-linux.tar.gz",
    },
}
ARCHIVE_EXTENSIONS = (".tar.gz", ".zip", ".tgz", ".tar", ".7z")


class PluginAutoUpdateService(
    AutoUpdateLoopMixin,
    AutoUpdateAssetsMixin,
    AutoUpdateApplyMixin,
    AutoUpdateCheckMixin,
):
    CHECK_LOOP_SECONDS: int = 60


plugin_auto_update_service = PluginAutoUpdateService()


async def record_framework_installation(server: Server, user: User, framework_key: str) -> None:
    config = FRAMEWORKS[framework_key]
    probe = ManagedPlugin(
        server_id=server.id,
        source_type="framework",
        source_key=framework_key,
        display_name=config["name"],
        repo_url=config["repo_url"],
        framework_key=framework_key,
        asset_glob=config["asset_glob"],
    )
    if framework_key == "metamod":
        ok, latest, _ = await plugin_auto_update_service._latest_metamod(server)
    else:
        ok, latest, _ = await plugin_auto_update_service._latest_github_release(probe, user)
    await upsert_managed_plugin(
        server_id=server.id,
        source_type="framework",
        source_key=framework_key,
        display_name=config["name"],
        repo_url=config["repo_url"],
        framework_key=framework_key,
        installed_release_id=latest["release_id"] if ok and latest else None,
        installed_version=latest["version"] if ok and latest else "unknown",
        asset_glob=config["asset_glob"],
    )


async def record_known_github_installation(
    server: Server, user: User, repo_url: str, display_name: str, asset_glob: str
) -> None:
    canonical = canonical_repo_url(repo_url)
    probe = ManagedPlugin(
        server_id=server.id,
        source_type="github",
        source_key=canonical.lower(),
        display_name=display_name,
        repo_url=canonical,
        asset_glob=asset_glob,
    )
    ok, latest, _ = await plugin_auto_update_service._latest_github_release(probe, user)
    await upsert_managed_plugin(
        server_id=server.id,
        source_type="github",
        source_key=canonical.lower(),
        display_name=display_name,
        repo_url=canonical,
        installed_release_id=latest["release_id"] if ok and latest else None,
        installed_version=latest["version"] if ok and latest else "unknown",
        asset_glob=asset_glob,
    )


__all__ = [
    "ARCHIVE_EXTENSIONS",
    "CONFIG_EXCLUSIONS",
    "FRAMEWORKS",
    "PluginAutoUpdateService",
    "canonical_repo_url",
    "derive_asset_glob",
    "plugin_auto_update_service",
    "record_framework_installation",
    "record_known_github_installation",
    "upsert_managed_plugin",
]
