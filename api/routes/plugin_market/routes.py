"""Legacy plugin-market HTTP routes registered in original contract order."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from sqlmodel import select

from api.dependencies import ActiveUser, AdminUser, DatabaseSession, LockedServerOperation
from modules import (
    ActionResponse,
    GitHubPluginInstallResponse,
    GitHubRepoInfo,
    MarketPlugin,
    MarketPluginCreate,
    MarketPluginListResponse,
    MarketPluginResponse,
    MarketPluginUpdate,
    PluginCategory,
    PluginConflictRule,
    PluginConflictRuleResponse,
    PluginConflictRulesUpdate,
    PluginUninstallRequest,
)
from services.compat import LateBoundModule
from services.plugin_conflict_service import PluginPlanError
from services.plugins.catalog_fields import apply_market_plugin_update

logger = logging.getLogger(__name__)
host = LateBoundModule("api.routes.plugin_market")
router = APIRouter(prefix="/api/plugin-market", tags=["plugin-market"])


@router.get("/plugins", response_model=MarketPluginListResponse)
async def list_plugins(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    category: Optional[str] = Query(None, description="Filter by category"),
    framework: Optional[str] = Query(
        None, description="Filter by marketplace section (counterstrikesharp or swiftly)"
    ),
    search: Optional[str] = Query(None, description="Search query"),
    *,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> MarketPluginListResponse:
    """
    List plugins from the market with pagination, filtering, and search.

    Args:
        page: Page number (starts from 1)
        page_size: Number of items per page
        category: Optional category filter
        framework: Optional marketplace section filter
        search: Optional search query (searches in title, description, author)

    Returns:
        List of plugins with pagination info
    """
    category_enum = None
    if category:
        try:
            category_enum = PluginCategory(category)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Invalid category. Valid categories: "
                    + ", ".join([c.value for c in PluginCategory])
                ),
            ) from None

    framework_enum = None
    if framework:
        try:
            framework_enum = host.parse_framework(framework)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    skip = (page - 1) * page_size
    plugins, total = await MarketPlugin.search_plugins(
        db,
        category=category_enum,
        search_query=search,
        skip=skip,
        limit=page_size,
        framework=framework_enum,
    )
    total_pages = (total + page_size - 1) // page_size
    plugin_responses = await host.populate_dependency_details(db, plugins)
    return MarketPluginListResponse(
        success=True,
        plugins=plugin_responses,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/plugins/{plugin_id}", response_model=MarketPluginResponse)
async def get_plugin(
    plugin_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> MarketPluginResponse:
    """
    Get details of a specific plugin.

    Args:
        plugin_id: Plugin ID

    Returns:
        Plugin details
    """
    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if not plugin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    plugin_responses = await host.populate_dependency_details(db, [plugin])
    return plugin_responses[0]


@router.post("/plugins", response_model=MarketPluginResponse)
async def create_plugin(
    request: MarketPluginCreate,
    db: DatabaseSession,
    current_user: AdminUser,
) -> MarketPluginResponse:
    """
    Add a new plugin to the market (admin only).

    Auto-fetches repository info if title/description not provided.

    Args:
        request: Plugin creation request

    Returns:
        Created plugin
    """
    existing = await MarketPlugin.get_by_github_url(db, request.github_url)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Plugin with this GitHub URL already exists",
        )

    title = request.title
    description = request.description
    author = request.author

    if not title or not description:
        github_token = await host.get_effective_github_token(db, current_user)
        await db.commit()
        repo_info = await host.fetch_github_repo_info(request.github_url, github_token=github_token)
        if repo_info.success:
            if not title and repo_info.repo_name:
                title = repo_info.repo_name
            if not description and repo_info.description:
                description = repo_info.description
            if not author and repo_info.author:
                author = repo_info.author

    try:
        category_enum = PluginCategory(request.category)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid category. Valid categories: "
                + ", ".join([c.value for c in PluginCategory])
            ),
        ) from None

    try:
        framework_enum = host.parse_framework(request.framework)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if request.dependencies:
        try:
            dep_ids = host.parse_dependency_ids(request.dependencies)
            await host.validate_dependencies(db, dep_ids)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    plugin = MarketPlugin(
        github_url=request.github_url,
        title=title or "Untitled Plugin",
        description=description,
        author=author,
        version=request.version,
        category=category_enum,
        framework=framework_enum,
        tags=request.tags,
        is_recommended=request.is_recommended,
        icon_url=request.icon_url,
        dependencies=request.dependencies,
        custom_install_path=request.custom_install_path,
    )
    db.add(plugin)
    await db.commit()
    await db.refresh(plugin)
    logger.info("Plugin '%s' added to market by admin %s", plugin.title, current_user.username)
    return MarketPluginResponse.model_validate(plugin)


@router.put("/plugins/{plugin_id}", response_model=MarketPluginResponse)
async def update_plugin(
    plugin_id: int,
    request: MarketPluginUpdate,
    db: DatabaseSession,
    current_user: AdminUser,
) -> MarketPluginResponse:
    """
    Update a plugin in the market (admin only).

    Args:
        plugin_id: Plugin ID
        request: Plugin update request

    Returns:
        Updated plugin
    """
    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if not plugin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    if request.dependencies:
        try:
            dep_ids = host.parse_dependency_ids(request.dependencies)
            await host.validate_dependencies(db, dep_ids)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    try:
        apply_market_plugin_update(plugin, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    db.add(plugin)
    await db.commit()
    await db.refresh(plugin)
    logger.info("Plugin '%s' updated by admin %s", plugin.title, current_user.username)
    return MarketPluginResponse.model_validate(plugin)


@router.delete("/plugins/{plugin_id}", response_model=ActionResponse)
async def delete_plugin(
    plugin_id: int,
    db: DatabaseSession,
    current_user: AdminUser,
) -> ActionResponse:
    """
    Delete a plugin from the market (admin only).

    Args:
        plugin_id: Plugin ID

    Returns:
        Success response
    """
    plugin = await host.delete_market_plugin(db, plugin_id)
    if not plugin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    logger.info("Plugin '%s' deleted by admin %s", plugin.title, current_user.username)
    return ActionResponse(success=True, message=f"Plugin '{plugin.title}' deleted successfully")


@router.get("/plugins/{plugin_id}/releases")
async def get_plugin_releases(
    plugin_id: int,
    server_id: Optional[int] = Query(None, description="Optional server ID for GitHub proxy"),
    count: int = Query(5, ge=1, le=10, description="Number of releases to fetch"),
    *,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """
    Fetch available releases for a market plugin.

    Args:
        plugin_id: Plugin ID from market
        server_id: Optional server ID to use server's GitHub proxy
        count: Number of releases to fetch (max 10)

    Returns:
        List of releases with download URLs
    """
    from api.routes.github_plugins import get_github_releases

    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if not plugin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    return await get_github_releases(
        repo_url=plugin.github_url,
        count=count,
        server_id=server_id,
        db=db,
        current_user=current_user,
    )


@router.get("/plugins/{plugin_id}/install-preflight")
async def plugin_install_preflight(
    plugin_id: int,
    server_id: int = Query(..., description="Target server ID"),
    install_dependencies: bool = Query(default=True),
    *,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict:
    """Resolve dependencies and conflicts without changing the server."""
    server = await host.get_server_for_user(server_id, db, current_user)
    try:
        return await host.build_plugin_install_plan(
            db,
            server_id,
            plugin_id,
            include_dependencies=install_dependencies,
            server=server,
        )
    except PluginPlanError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get(
    "/plugins/{plugin_id}/conflicts",
    response_model=list[PluginConflictRuleResponse],
)
async def get_plugin_conflict_rules(
    plugin_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> list[PluginConflictRule]:
    if await MarketPlugin.get_by_id(db, plugin_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    result = await db.execute(
        select(PluginConflictRule).where(
            (PluginConflictRule.plugin_a_id == plugin_id)
            | (PluginConflictRule.plugin_b_id == plugin_id)
        )
    )
    return list(result.scalars().all())


@router.put(
    "/plugins/{plugin_id}/conflicts",
    response_model=list[PluginConflictRuleResponse],
)
async def replace_plugin_conflict_rules(
    plugin_id: int,
    request: PluginConflictRulesUpdate,
    db: DatabaseSession,
    current_user: AdminUser,
) -> list[PluginConflictRule]:
    """Atomically replace every conflict rule attached to one plugin."""
    if await MarketPlugin.get_by_id(db, plugin_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    other_ids = [item.other_plugin_id for item in request.rules]
    if plugin_id in other_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A plugin cannot conflict with itself",
        )
    if len(set(other_ids)) != len(other_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Duplicate conflict pair",
        )
    found = await MarketPlugin.get_by_ids(db, other_ids)
    if {item.id for item in found} != set(other_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="One or more conflict plugins do not exist",
        )

    existing_result = await db.execute(
        select(PluginConflictRule).where(
            (PluginConflictRule.plugin_a_id == plugin_id)
            | (PluginConflictRule.plugin_b_id == plugin_id)
        )
    )
    for existing in existing_result.scalars().all():
        await db.delete(existing)
    await db.flush()
    created: list[PluginConflictRule] = []
    for item in request.rules:
        plugin_a_id, plugin_b_id = sorted((plugin_id, item.other_plugin_id))
        rule = PluginConflictRule(
            plugin_a_id=plugin_a_id,
            plugin_b_id=plugin_b_id,
            severity=item.severity,
            reason=item.reason.strip(),
            is_enabled=item.is_enabled,
        )
        db.add(rule)
        created.append(rule)
    await db.commit()
    for rule in created:
        await db.refresh(rule)
    return created


@router.post("/plugins/{plugin_id}/install", response_model=GitHubPluginInstallResponse)
async def install_plugin(
    plugin_id: int,
    server_id: int = Query(..., description="Server ID to install plugin on"),
    download_url: Optional[str] = Query(
        None, description="Specific release download URL (if not provided, uses latest)"
    ),
    exclude_dirs: list[str] = Query(
        default=[], description="Directories to exclude (deprecated, use exclude_files)"
    ),
    exclude_files: list[str] = Query(default=[], description="Files to exclude from installation"),
    install_dependencies: bool = Query(
        default=False, description="Whether to install dependencies"
    ),
    acknowledge_warning_rule_ids: list[int] = Query(
        default=[], description="Current soft-conflict rule IDs explicitly acknowledged"
    ),
    acknowledge_framework_mismatch: bool = Query(
        default=False,
        description="Install even though the server runs the other plugin runtime",
    ),
    upgrade_mode: bool = Query(
        default=False, description="Enable upgrade mode to auto-exclude config files"
    ),
    *,
    db: DatabaseSession,
    current_user: ActiveUser,
    _operation_server: LockedServerOperation,
) -> GitHubPluginInstallResponse:
    """
    Install a plugin from the market to a server.

    This endpoint:
    1. Checks SSH connectivity to server first
    2. Fetches the plugin from market
    3. Installs dependencies first (if any and install_dependencies=True)
    4. Gets the specified release or latest release from GitHub
    5. Installs using the existing GitHub plugin installation logic

    Args:
        plugin_id: Plugin ID from market
        server_id: Server ID to install on
        download_url: Optional specific release download URL (if not provided, uses latest)
        exclude_dirs: Optional directories to exclude from extraction (deprecated)
        exclude_files: Optional files to exclude from extraction
        install_dependencies: Whether to automatically install dependencies
        upgrade_mode: When enabled, auto-excludes common config files (.ini, .cfg, .json, etc.)

    Returns:
        Installation result
    """
    selected_release_id, selected_release_tag, selected_asset_name = host._requested_release(
        download_url
    )
    linux_runtime_profile = None

    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if not plugin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    server = await host.get_server_for_user(server_id, db, current_user)

    try:
        install_plan = await host.build_plugin_install_plan(
            db,
            server_id,
            plugin_id,
            include_dependencies=install_dependencies,
            server=server,
        )
        host.validate_plugin_plan_acknowledgements(
            install_plan,
            acknowledge_warning_rule_ids,
            acknowledge_framework_mismatch=acknowledge_framework_mismatch,
        )
    except PluginPlanError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    ssh_success, ssh_msg = await host._check_plugin_ssh(server)
    if not ssh_success:
        return GitHubPluginInstallResponse(
            success=False,
            message=(
                "Cannot connect to server via SSH: "
                f"{ssh_msg}. Please check server connectivity before installing plugins."
            ),
        )

    (
        download_url,
        selected_release_id,
        selected_release_tag,
        selected_asset_name,
        resolve_error,
        linux_runtime_profile,
    ) = await host._resolve_market_asset(
        plugin,
        server,
        db,
        current_user,
        download_url,
        selected_asset_name,
        linux_runtime_profile,
    )
    if download_url is None:
        return GitHubPluginInstallResponse(
            success=False,
            message=resolve_error or "No suitable release asset found for installation",
        )

    installed_deps, dependency_error = (
        await host._install_dependencies(
            install_plugin,
            install_plan,
            plugin_id,
            server_id,
            exclude_dirs,
            exclude_files,
            acknowledge_warning_rule_ids,
            upgrade_mode,
            db,
            current_user,
            _operation_server,
        )
        if install_dependencies
        else ([], None)
    )
    if dependency_error is not None:
        return dependency_error

    plan_error = await host._validate_latest_target_plan(
        db, server_id, plugin_id, server, acknowledge_warning_rule_ids
    )
    if plan_error:
        return GitHubPluginInstallResponse(
            success=False,
            message=(
                f"Plugin rules changed before the target install: {plan_error}. "
                f"Completed dependencies: {', '.join(installed_deps) or 'none'}"
            ),
        )

    return await host._execute_market_install(
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
    )


@router.get("/categories")
async def list_categories(current_user: ActiveUser) -> dict:
    """
    Get list of available plugin categories.

    Returns:
        List of category values and names
    """
    categories = [
        {"value": c.value, "name": c.value.replace("_", " ").title()} for c in PluginCategory
    ]
    return {"success": True, "categories": categories}


@router.get("/plugins-for-dependencies")
async def list_plugins_for_dependencies(
    exclude_id: Optional[int] = Query(None, description="Plugin ID to exclude (for editing)"),
    search: Optional[str] = Query(None, description="Search query for filtering plugins"),
    *,
    db: DatabaseSession,
    current_user: AdminUser,
) -> dict:
    """
    Get list of plugins for dependency selection (admin only).
    Returns only essential fields for efficiency.
    Supports backend search for better performance with large plugin lists.

    Args:
        exclude_id: Optional plugin ID to exclude (prevents self-dependency when editing)
        search: Optional search query to filter plugins by title

    Returns:
        List of plugins with id and title only
    """
    plugins, _ = await MarketPlugin.search_plugins(
        db,
        search_query=search,
        skip=0,
        limit=100,
    )
    plugin_list = [
        {"id": p.id, "title": p.title} for p in plugins if exclude_id is None or p.id != exclude_id
    ]
    return {"success": True, "plugins": plugin_list}


@router.get("/plugins/{plugin_id}/analyze-archive")
async def analyze_plugin_archive(
    plugin_id: int,
    server_id: int = Query(..., description="Server ID for analysis"),
    download_url: Optional[str] = Query(
        None, description="Specific release download URL (if not provided, uses latest)"
    ),
    *,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """
    Analyze a plugin archive to show its directory structure.
    This allows users to select which directories to exclude during installation.

    Args:
        plugin_id: Plugin ID from market
        server_id: Server ID for SSH connection
        download_url: Optional specific release download URL (if not provided, uses latest)

    Returns:
        Archive analysis with directory structure
    """
    from api.routes.github_plugins import analyze_archive as analyze_github_archive

    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if not plugin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    server = await host.get_server_for_user(server_id, db, current_user)

    if not download_url:
        try:
            owner, repo = host.parse_github_url(plugin.github_url)
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
            headers = {"Accept": "application/vnd.github+json", "User-Agent": "CS2-ServerManager"}
            github_token = await host.get_effective_github_token(db, current_user)
            success, data, error = await host.http_helper.get(
                api_url,
                headers=headers,
                timeout=30,
                proxy=server.github_proxy,
                github_token=github_token,
            )

            if not success or not isinstance(data, dict):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to fetch latest release: {error}",
                )

            assets = data.get("assets", [])
            for asset in assets if isinstance(assets, list) else []:
                if not isinstance(asset, dict):
                    continue
                asset_name = str(asset.get("name") or "").lower()
                if (
                    "windows" in asset_name
                    or "-win-" in asset_name
                    or "_win_" in asset_name
                    or asset_name.endswith("-win.zip")
                ):
                    continue
                if any(
                    asset_name.endswith(ext) for ext in [".zip", ".tar.gz", ".tgz", ".tar", ".7z"]
                ):
                    candidate_url = asset.get("browser_download_url")
                    if isinstance(candidate_url, str):
                        download_url = candidate_url
                        break

            if not download_url:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="No suitable release asset found"
                )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    return await analyze_github_archive(
        server_id=server_id, download_url=download_url, db=db, current_user=current_user
    )


@router.post("/fetch-repo-info", response_model=GitHubRepoInfo)
async def fetch_repo_info(
    github_url: str = Query(..., description="GitHub repository URL"),
    *,
    db: DatabaseSession,
    current_user: AdminUser,
) -> GitHubRepoInfo:
    """
    Fetch repository information from GitHub (admin only).
    Helper endpoint for auto-filling plugin details.

    Args:
        github_url: GitHub repository URL

    Returns:
        Repository information
    """
    github_token = await host.get_effective_github_token(db, current_user)
    return await host.fetch_github_repo_info(github_url, github_token=github_token)


@router.post("/plugins/{plugin_id}/uninstall")
async def uninstall_market_plugin(
    plugin_id: int,
    server_id: int,
    request: PluginUninstallRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    _operation_server: LockedServerOperation,
):
    """
    Uninstall a market plugin from a server.

    This is a wrapper around the GitHub plugin uninstall endpoint that:
    1. Verifies the plugin exists in the market
    2. Calls the uninstall function with the provided file list

    Args:
        plugin_id: Plugin ID from market
        server_id: Server ID to uninstall from (query parameter)
        request: Uninstall request with list of files to delete

    Returns:
        Uninstallation result
    """
    from api.routes.github_plugins import uninstall_plugin
    from modules.models import ManagedPlugin

    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if not plugin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    await host.get_server_for_user(server_id, db, current_user)

    result = await uninstall_plugin(server_id, request, db, current_user, _operation_server)
    if result.success:
        tracked = await db.execute(
            select(ManagedPlugin).where(
                ManagedPlugin.server_id == server_id,
                ManagedPlugin.source_type == "market",
                ManagedPlugin.source_key == str(plugin_id),
            )
        )
        managed = tracked.scalar_one_or_none()
        if managed:
            await db.delete(managed)
            await db.commit()
    return result
