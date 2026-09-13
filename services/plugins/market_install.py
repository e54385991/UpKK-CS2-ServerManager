"""Marketplace install execution shared by the legacy plugin-market routes."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from modules import (
    GitHubPluginInstallRequest,
    GitHubPluginInstallResponse,
    MarketPlugin,
    Server,
    User,
)
from services.plugins.common import PluginPlanError
from services.plugins.upgrade_exclusions import (
    CONFIG_FILE_EXTENSIONS,
    apply_upgrade_mode_exclusions,
)

logger = logging.getLogger(__name__)

InstallPlugin = Callable[..., Awaitable[GitHubPluginInstallResponse]]


async def check_plugin_ssh(server: Server) -> tuple[bool, str]:
    from services import SSHManager

    ssh_manager = SSHManager()
    try:
        return await ssh_manager.connect(server)
    finally:
        await ssh_manager.disconnect()


async def validate_latest_target_plan(
    db: AsyncSession,
    server_id: int,
    plugin_id: int,
    server: Server,
    acknowledgements: list[int],
    *,
    plan_builder=None,
    plan_validator=None,
) -> str | None:
    from services.plugin_conflict_service import (
        build_plugin_install_plan,
        validate_plugin_plan_acknowledgements,
    )

    builder = build_plugin_install_plan if plan_builder is None else plan_builder
    validator = validate_plugin_plan_acknowledgements if plan_validator is None else plan_validator
    try:
        plan = await builder(db, server_id, plugin_id, include_dependencies=False, server=server)
        validator(plan, acknowledgements)
    except PluginPlanError as exc:
        return str(exc)
    return None


async def execute_market_install(
    plugin: MarketPlugin,
    server_id: int,
    server: Server,
    download_url: str | None,
    selected_release_id: str | None,
    selected_release_tag: str | None,
    selected_asset_name: str | None,
    exclude_dirs: list[str],
    exclude_files: list[str],
    upgrade_mode: bool,
    db: AsyncSession,
    current_user: User,
    installed_deps: list[str],
    *,
    installer: InstallPlugin | None = None,
    apply_exclusions=apply_upgrade_mode_exclusions,
) -> GitHubPluginInstallResponse:
    """Run the mutating install and persist managed-plugin metadata."""
    from services.plugin_installation import install_github_plugin

    active_installer = install_github_plugin if installer is None else installer
    try:
        plugin.download_count += 1
        db.add(plugin)
        await db.commit()
    except Exception as exc:
        logger.error("Failed to update download count: %s", exc)
        await db.rollback()

    await db.refresh(plugin)
    final_exclude_files = list(exclude_files)
    if upgrade_mode:
        final_exclude_files = apply_exclusions(final_exclude_files)
        logger.info(
            "Upgrade mode enabled: auto-excluding config files with extensions %s",
            CONFIG_FILE_EXTENSIONS,
        )

    if not download_url:
        raise LookupError("No suitable release asset found")
    install_request = GitHubPluginInstallRequest(
        download_url=download_url,
        exclude_dirs=exclude_dirs,
        exclude_files=final_exclude_files,
        custom_install_path=plugin.custom_install_path,
    )
    result = await active_installer(server_id, install_request, db, current_user)
    if result.success:
        try:
            plugin.install_count += 1
            db.add(plugin)
            await db.commit()
        except Exception as exc:
            logger.error("Failed to update install count: %s", exc)
            await db.rollback()

        from services.plugin_auto_update_service import derive_asset_glob, upsert_managed_plugin

        await upsert_managed_plugin(
            server_id=server.id,
            source_type="market",
            source_key=str(plugin.id),
            display_name=plugin.title,
            repo_url=plugin.github_url,
            market_plugin_id=plugin.id,
            installed_release_id=selected_release_id,
            installed_version=selected_release_tag or plugin.version or "unknown",
            installed_asset_name=selected_asset_name,
            asset_glob=derive_asset_glob(selected_asset_name, selected_release_tag),
            custom_install_path=plugin.custom_install_path,
            exclude_dirs=exclude_dirs,
            exclude_files=final_exclude_files,
        )
        if installed_deps:
            result.message += f" (Dependencies also installed: {', '.join(installed_deps)})"
    return result
