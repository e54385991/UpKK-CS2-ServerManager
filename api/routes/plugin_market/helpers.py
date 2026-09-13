"""Patchable helpers for the legacy plugin-market route facade."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from modules import GitHubPluginInstallResponse, MarketPlugin, Server, User
from services.compat import LateBoundModule
from services.linux_runtime_service import RuntimeSelectionRequired
from services.plugins.common import parse_dependency_ids as parse_declared_dependency_ids
from services.plugins.market_assets import requested_release, resolve_market_asset
from services.plugins.market_assets import resolve_latest_market_asset as resolve_asset
from services.plugins.market_catalog import MissingDependencyError
from services.plugins.market_catalog import (
    populate_dependency_details as populate_catalog_dependencies,
)
from services.plugins.market_catalog import validate_dependencies as confirm_dependencies
from services.plugins.market_install import (
    check_plugin_ssh,
    execute_market_install,
    validate_latest_target_plan,
)

logger = logging.getLogger(__name__)
host = LateBoundModule("api.routes.plugin_market")


def parse_dependency_ids(dependencies: Optional[str]) -> list[int]:
    """Keep repeated marketplace dependency IDs in declared order."""
    return parse_declared_dependency_ids(dependencies, unique=False)


def _requested_release(download_url: str | None) -> tuple[str | None, str | None, str | None]:
    try:
        return requested_release(download_url)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def resolve_latest_market_asset(
    plugin: MarketPlugin,
    server: Server,
    db: AsyncSession,
    current_user: User,
    linux_runtime_profile: dict | None,
) -> tuple[Optional[dict], Optional[str]]:
    return await resolve_asset(
        plugin,
        server,
        db,
        current_user,
        linux_runtime_profile,
        parse_url=host.parse_github_url,
        token_getter=host.get_effective_github_token,
        http=host.http_helper,
    )


async def _resolve_market_asset(
    plugin: MarketPlugin,
    server: Server,
    db: AsyncSession,
    current_user: User,
    download_url: str | None,
    selected_asset_name: str | None,
    runtime_profile,
):
    try:
        return await resolve_market_asset(
            plugin,
            server,
            db,
            current_user,
            download_url,
            selected_asset_name,
            runtime_profile,
            resolve_latest=host.resolve_latest_market_asset,
        )
    except RuntimeSelectionRequired as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


async def validate_dependencies(db: AsyncSession, dependency_ids: list[int]) -> None:
    try:
        await confirm_dependencies(db, dependency_ids)
    except MissingDependencyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


async def populate_dependency_details(db: AsyncSession, plugins: list[MarketPlugin]):
    return await populate_catalog_dependencies(db, plugins)


async def _check_plugin_ssh(server: Server) -> tuple[bool, str]:
    return await check_plugin_ssh(server)


async def _validate_latest_target_plan(
    db: AsyncSession,
    server_id: int,
    plugin_id: int,
    server: Server,
    acknowledgements: list[int],
) -> str | None:
    return await validate_latest_target_plan(
        db,
        server_id,
        plugin_id,
        server,
        acknowledgements,
        plan_builder=host.build_plugin_install_plan,
        plan_validator=host.validate_plugin_plan_acknowledgements,
    )


async def _execute_market_install(
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
) -> GitHubPluginInstallResponse:
    try:
        return await execute_market_install(
            plugin,
            server_id,
            server,
            download_url,
            selected_release_id,
            selected_release_tag,
            selected_asset_name,
            exclude_dirs,
            exclude_files,
            upgrade_mode,
            db,
            current_user,
            installed_deps,
            installer=host.install_github_plugin,
            apply_exclusions=host.apply_upgrade_mode_exclusions,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error installing plugin: %s", exc, exc_info=True)
        message = f"Installation error: {exc}"
        if installed_deps:
            message += f" (Dependencies installed: {', '.join(installed_deps)})"
        return GitHubPluginInstallResponse(success=False, message=message)


async def _install_dependencies(
    install_plugin_fn,
    install_plan: dict,
    plugin_id: int,
    server_id: int,
    exclude_dirs: list[str],
    exclude_files: list[str],
    acknowledge_warning_rule_ids: list[int],
    upgrade_mode: bool,
    db: AsyncSession,
    current_user: User,
    operation_server,
) -> tuple[list[str], GitHubPluginInstallResponse | None]:
    installed: list[str] = []
    if not install_plan["dependencies"]:
        return installed, None
    try:
        dep_ids = [
            item
            for item in install_plan["installation_order"]
            if item != plugin_id and item not in install_plan["already_installed"]
        ]
        dependencies = await MarketPlugin.get_by_ids(db, dep_ids)
        dependencies_by_id = {dependency.id: dependency for dependency in dependencies}
        for dep_id in dep_ids:
            dep_plugin = dependencies_by_id.get(dep_id)
            if dep_plugin is None:
                continue
            logger.info("Installing dependency: %s", dep_plugin.title)
            try:
                dep_result = await install_plugin_fn(
                    dep_id,
                    server_id,
                    download_url=None,
                    exclude_dirs=exclude_dirs,
                    exclude_files=exclude_files,
                    install_dependencies=False,
                    acknowledge_warning_rule_ids=acknowledge_warning_rule_ids,
                    upgrade_mode=upgrade_mode,
                    db=db,
                    current_user=current_user,
                    _operation_server=operation_server,
                )
            except HTTPException as exc:
                return installed, GitHubPluginInstallResponse(
                    success=False,
                    message=(
                        f"Dependency {dep_plugin.title} stopped: {exc.detail}. "
                        f"Completed dependencies: {', '.join(installed) or 'none'}"
                    ),
                )
            if not dep_result.success:
                return installed, GitHubPluginInstallResponse(
                    success=False,
                    message=(
                        f"Dependency {dep_plugin.title} failed: {dep_result.message}. "
                        f"Completed dependencies: {', '.join(installed) or 'none'}"
                    ),
                )
            installed.append(dep_plugin.title)
    except ValueError as exc:
        logger.error("Error parsing dependencies: %s", exc)
    return installed, None
