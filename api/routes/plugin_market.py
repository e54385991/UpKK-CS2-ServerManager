"""
Plugin Market routes
Provides endpoints for browsing, searching, and installing plugins from the market
"""

import logging
import re
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.dependencies import (
    ActiveUser,
    AdminUser,
    DatabaseSession,
    LockedServerOperation,
)
from modules import (
    ActionResponse,
    DependencyInfo,
    GitHubPluginInstallRequest,
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
    Server,
    User,
)
from modules.http_helper import http_helper
from services.github_credentials import get_effective_github_token
from services.plugin_catalog import delete_market_plugin
from services.plugin_conflict_service import (
    PluginPlanError,
    build_plugin_install_plan,
    validate_plugin_plan_acknowledgements,
)
from services.plugin_installation import install_github_plugin
from services.plugins.upgrade_exclusions import (
    CONFIG_FILE_EXTENSIONS,
    apply_upgrade_mode_exclusions,
)

router = APIRouter(prefix="/api/plugin-market", tags=["plugin-market"])

logger = logging.getLogger(__name__)

# Regex to validate GitHub repository URL (supports both https and git formats)
GITHUB_REPO_PATTERN = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+?)(?:\.git)?(?:/.*)?$"
)


def _requested_release(download_url: str | None) -> tuple[str | None, str | None, str | None]:
    if not download_url:
        return None, None, None
    if (
        not download_url.startswith("https://github.com/")
        or "/releases/download/" not in download_url
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid download URL. Must be a GitHub releases download URL.",
        )
    release_parts = download_url.split("/releases/download/", 1)[1].split("/", 1)
    if len(release_parts) != 2:
        return None, None, None
    tag, asset = release_parts
    return f"tag:{tag}", tag, asset


async def get_server_for_user(server_id: int, db: AsyncSession, current_user: User) -> Server:
    """Helper to get server and verify ownership - admins can access any server"""
    if current_user.is_admin:
        server = await Server.get_by_id(db, server_id)
    else:
        server = await Server.get_by_id_and_user(db, server_id, current_user.id)

    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    # End the read transaction before potentially long SSH/GitHub work.
    await db.commit()
    return server


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
                    message=f"Dependency {dep_plugin.title} stopped: {exc.detail}. Completed dependencies: {', '.join(installed) or 'none'}",
                )
            if not dep_result.success:
                return installed, GitHubPluginInstallResponse(
                    success=False,
                    message=f"Dependency {dep_plugin.title} failed: {dep_result.message}. Completed dependencies: {', '.join(installed) or 'none'}",
                )
            installed.append(dep_plugin.title)
    except ValueError as exc:
        logger.error("Error parsing dependencies: %s", exc)
    return installed, None


async def _check_plugin_ssh(server: Server) -> tuple[bool, str]:
    from services import SSHManager

    ssh_manager = SSHManager()
    try:
        return await ssh_manager.connect(server)
    finally:
        await ssh_manager.disconnect()


async def _resolve_market_asset(
    plugin: MarketPlugin,
    server: Server,
    db: AsyncSession,
    current_user: User,
    download_url: str | None,
    selected_asset_name: str | None,
    runtime_profile,
) -> tuple[str | None, str | None, str | None, str | None, str | None, object]:
    if download_url:
        return download_url, None, None, selected_asset_name, None, runtime_profile

    from services.linux_runtime_service import (
        RuntimeSelectionRequired,
        detect_linux_runtime_profile,
    )

    runtime_profile = await detect_linux_runtime_profile(server)
    try:
        asset, error = await resolve_latest_market_asset(
            plugin, server, db, current_user, runtime_profile
        )
    except RuntimeSelectionRequired as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if asset is None:
        return (
            None,
            None,
            None,
            None,
            error or "No suitable release asset found for installation",
            runtime_profile,
        )
    return (
        asset["download_url"],
        asset["release_id"],
        asset["release_tag"],
        asset["asset_name"],
        None,
        runtime_profile,
    )


async def _validate_latest_target_plan(
    db: AsyncSession,
    server_id: int,
    plugin_id: int,
    server: Server,
    acknowledgements: list[int],
) -> str | None:
    try:
        plan = await build_plugin_install_plan(
            db, server_id, plugin_id, include_dependencies=False, server=server
        )
        validate_plugin_plan_acknowledgements(plan, acknowledgements)
    except PluginPlanError as exc:
        return str(exc)
    return None


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
    """Run the mutating install and persist managed-plugin metadata."""
    try:
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
            final_exclude_files = apply_upgrade_mode_exclusions(final_exclude_files)
            logger.info(
                "Upgrade mode enabled: auto-excluding config files with extensions %s",
                CONFIG_FILE_EXTENSIONS,
            )

        if not download_url:
            raise HTTPException(status_code=404, detail="No suitable release asset found")
        install_request = GitHubPluginInstallRequest(
            download_url=download_url,
            exclude_dirs=exclude_dirs,
            exclude_files=final_exclude_files,
            custom_install_path=plugin.custom_install_path,
        )
        result = await install_github_plugin(server_id, install_request, db, current_user)
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
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error installing plugin: %s", exc, exc_info=True)
        message = f"Installation error: {exc}"
        if installed_deps:
            message += f" (Dependencies installed: {', '.join(installed_deps)})"
        return GitHubPluginInstallResponse(success=False, message=message)


def parse_github_url(url: str) -> tuple[str, str]:
    """
    Parse GitHub repository URL to extract owner and repo name.
    Supports both https:// and git@ formats.

    Args:
        url: GitHub repository URL

    Returns:
        Tuple of (owner, repo_name)

    Raises:
        ValueError: If URL is invalid
    """
    match = GITHUB_REPO_PATTERN.match(url)
    if not match:
        raise ValueError("Invalid GitHub repository URL format")
    return match.group(1), match.group(2)


def parse_dependency_ids(dependencies: Optional[str]) -> list[int]:
    """
    Parse comma-separated dependency IDs into a list of integers.

    Args:
        dependencies: Comma-separated plugin IDs or None

    Returns:
        List of plugin IDs as integers

    Raises:
        ValueError: If any dependency ID is invalid
    """
    if not dependencies:
        return []

    dep_ids = []
    for dep in dependencies.split(","):
        dep = dep.strip()
        if not dep:
            continue
        if not dep.isdigit():
            raise ValueError(f"Invalid dependency ID: {dep}")
        dep_ids.append(int(dep))

    return dep_ids


async def resolve_latest_market_asset(
    plugin: MarketPlugin,
    server: Server,
    db: AsyncSession,
    current_user: User,
    linux_runtime_profile: dict | None,
) -> tuple[Optional[dict], Optional[str]]:
    """Resolve the latest compatible archive before dependencies change the server."""
    owner, repo = parse_github_url(plugin.github_url)
    github_token = await get_effective_github_token(db, current_user)
    success, data, error = await http_helper.get(
        f"https://api.github.com/repos/{owner}/{repo}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "CS2-ServerManager"},
        timeout=30,
        proxy=server.github_proxy,
        github_token=github_token,
    )
    if not success or not isinstance(data, dict):
        return None, f"Failed to fetch latest release: {error}"

    candidates = []
    for asset in data.get("assets", []):
        asset_name = str(asset.get("name") or "")
        lowered = asset_name.casefold()
        if (
            "windows" in lowered
            or "-win-" in lowered
            or "_win_" in lowered
            or lowered.endswith("-win.zip")
        ):
            continue
        if any(
            lowered.endswith(extension) for extension in (".zip", ".tar.gz", ".tgz", ".tar", ".7z")
        ):
            candidates.append(asset)

    from services.linux_runtime_service import (
        RuntimeSelectionRequired,
        has_paired_runtime_assets,
        select_unique_runtime_asset,
    )

    if not candidates:
        return None, "No suitable release asset found for installation"
    if has_paired_runtime_assets(candidates):
        selected = select_unique_runtime_asset(candidates, linux_runtime_profile)
        if selected is None:
            raise RuntimeSelectionRequired(
                "Multiple Steam Runtime package families are available; select an asset explicitly"
            )
    else:
        selected = candidates[0]
    download_url = str(selected.get("browser_download_url") or "")
    if not download_url:
        return None, "Selected release asset does not include a download URL"
    return (
        {
            "download_url": download_url,
            "release_id": str(data.get("id") or ""),
            "release_tag": str(data.get("tag_name") or "unknown"),
            "asset_name": str(selected.get("name") or ""),
        },
        None,
    )


async def validate_dependencies(db: AsyncSession, dependency_ids: list[int]) -> None:
    """
    Validate that all dependency plugin IDs exist in the database.

    Args:
        db: Database session
        dependency_ids: List of plugin IDs to validate

    Raises:
        HTTPException: If any dependency plugin is not found
    """
    dependencies = await MarketPlugin.get_by_ids(db, dependency_ids)
    existing_ids = {plugin.id for plugin in dependencies}
    missing_id = next((dep_id for dep_id in dependency_ids if dep_id not in existing_ids), None)
    if missing_id is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dependency plugin with ID {missing_id} not found",
        )


async def fetch_github_repo_info(
    github_url: str, github_proxy: Optional[str] = None, github_token: Optional[str] = None
) -> GitHubRepoInfo:
    """
    Fetch repository information from GitHub API.

    Args:
        github_url: GitHub repository URL
        github_proxy: Optional GitHub proxy URL
        github_token: Optional GitHub personal access token for authentication

    Returns:
        GitHubRepoInfo with parsed data
    """
    try:
        owner, repo = parse_github_url(github_url)
    except ValueError as e:
        return GitHubRepoInfo(success=False, error=str(e))

    # Fetch repo info from GitHub API
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "CS2-ServerManager"}

    success, data, error = await http_helper.get(
        api_url, headers=headers, timeout=30, proxy=github_proxy, github_token=github_token
    )

    if not success or not isinstance(data, dict):
        return GitHubRepoInfo(success=False, error=f"Failed to fetch repository info: {error}")

    # Extract repo name and description
    repo_name = data.get("name", repo)
    description = data.get("description", "")

    # Fetch README to get first 200 characters
    readme_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
    readme_success, readme_data, _ = await http_helper.get(
        readme_url, headers=headers, timeout=30, proxy=github_proxy, github_token=github_token
    )

    if readme_success and isinstance(readme_data, dict):
        # GitHub API returns base64-encoded content
        import base64

        content = readme_data.get("content", "")
        if content:
            try:
                decoded = base64.b64decode(content).decode("utf-8")
                # Remove markdown headers and extract first 200 chars
                lines = [
                    line.strip()
                    for line in decoded.split("\n")
                    if line.strip() and not line.strip().startswith("#")
                ]
                if lines:
                    description = " ".join(lines)[:200]
            except Exception as e:
                logger.warning(f"Failed to decode README: {e}")

    return GitHubRepoInfo(
        success=True,
        repo_name=repo_name,
        description=description if description else None,
        author=owner,
    )


async def populate_dependency_details(
    db: AsyncSession, plugins: List[MarketPlugin]
) -> List[MarketPluginResponse]:
    """
    Populate dependency details for a list of plugins.

    Args:
        db: Database session
        plugins: List of MarketPlugin objects

    Returns:
        List of MarketPluginResponse with dependency details populated
    """
    responses = []
    parsed_dependencies: list[Optional[list[int]]] = []
    all_dependency_ids: list[int] = []

    for plugin in plugins:
        if plugin.dependencies:
            try:
                dep_ids = parse_dependency_ids(plugin.dependencies)
            except ValueError:
                dep_ids = None
        else:
            dep_ids = []
        parsed_dependencies.append(dep_ids)
        if dep_ids:
            all_dependency_ids.extend(dep_ids)

    dependencies = await MarketPlugin.get_by_ids(db, all_dependency_ids)
    dependencies_by_id = {plugin.id: plugin for plugin in dependencies}

    for plugin, dep_ids in zip(plugins, parsed_dependencies, strict=True):
        response = MarketPluginResponse.model_validate(plugin)
        if dep_ids:
            dependency_details = [
                DependencyInfo(id=dep_plugin.id, title=dep_plugin.title)
                for dep_id in dep_ids
                if (dep_plugin := dependencies_by_id.get(dep_id)) is not None
            ]
            response.dependency_details = dependency_details or None

        responses.append(response)

    return responses


@router.get("/plugins", response_model=MarketPluginListResponse)
async def list_plugins(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    category: Optional[str] = Query(None, description="Filter by category"),
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
        search: Optional search query (searches in title, description, author)

    Returns:
        List of plugins with pagination info
    """
    # Validate category if provided
    category_enum = None
    if category:
        try:
            category_enum = PluginCategory(category)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid category. Valid categories: {', '.join([c.value for c in PluginCategory])}",
            ) from None

    # Calculate skip
    skip = (page - 1) * page_size

    # Search plugins
    plugins, total = await MarketPlugin.search_plugins(
        db, category=category_enum, search_query=search, skip=skip, limit=page_size
    )

    # Calculate total pages
    total_pages = (total + page_size - 1) // page_size

    # Populate dependency details for each plugin
    plugin_responses = await populate_dependency_details(db, plugins)

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

    # Populate dependency details
    plugin_responses = await populate_dependency_details(db, [plugin])
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
    # Check if plugin with same GitHub URL already exists
    existing = await MarketPlugin.get_by_github_url(db, request.github_url)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Plugin with this GitHub URL already exists",
        )

    # Auto-fetch repo info if title or description not provided
    title = request.title
    description = request.description
    author = request.author

    if not title or not description:
        github_token = await get_effective_github_token(db, current_user)
        # Release any read transaction before the external GitHub requests.
        await db.commit()
        repo_info = await fetch_github_repo_info(request.github_url, github_token=github_token)
        if repo_info.success:
            if not title and repo_info.repo_name:
                title = repo_info.repo_name
            if not description and repo_info.description:
                description = repo_info.description
            if not author and repo_info.author:
                author = repo_info.author

    # Validate category
    try:
        category_enum = PluginCategory(request.category)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid category. Valid categories: {', '.join([c.value for c in PluginCategory])}",
        ) from None

    # Validate dependencies if provided
    if request.dependencies:
        try:
            dep_ids = parse_dependency_ids(request.dependencies)
            await validate_dependencies(db, dep_ids)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    # Create plugin
    plugin = MarketPlugin(
        github_url=request.github_url,
        title=title or "Untitled Plugin",
        description=description,
        author=author,
        version=request.version,
        category=category_enum,
        tags=request.tags,
        is_recommended=request.is_recommended,
        icon_url=request.icon_url,
        dependencies=request.dependencies,
        custom_install_path=request.custom_install_path,
    )

    db.add(plugin)
    await db.commit()
    await db.refresh(plugin)

    logger.info(f"Plugin '{plugin.title}' added to market by admin {current_user.username}")

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

    # Update fields
    if request.title is not None:
        plugin.title = request.title
    if request.description is not None:
        plugin.description = request.description
    if request.author is not None:
        plugin.author = request.author
    if request.version is not None:
        plugin.version = request.version
    if request.category is not None:
        try:
            plugin.category = PluginCategory(request.category)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid category. Valid categories: {', '.join([c.value for c in PluginCategory])}",
            ) from None
    if request.tags is not None:
        plugin.tags = request.tags
    if request.is_recommended is not None:
        plugin.is_recommended = request.is_recommended
    if request.icon_url is not None:
        plugin.icon_url = request.icon_url
    if request.custom_install_path is not None:
        plugin.custom_install_path = request.custom_install_path
    if request.dependencies is not None:
        # Validate dependencies if provided
        if request.dependencies:
            try:
                dep_ids = parse_dependency_ids(request.dependencies)
                await validate_dependencies(db, dep_ids)
            except ValueError as e:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
        plugin.dependencies = request.dependencies

    db.add(plugin)
    await db.commit()
    await db.refresh(plugin)

    logger.info(f"Plugin '{plugin.title}' updated by admin {current_user.username}")

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
    plugin = await delete_market_plugin(db, plugin_id)
    if not plugin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    logger.info(f"Plugin '{plugin.title}' deleted by admin {current_user.username}")

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

    # Get plugin
    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if not plugin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    # Fetch releases using the existing github_plugins endpoint logic
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
    server = await get_server_for_user(server_id, db, current_user)
    try:
        return await build_plugin_install_plan(
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
    # Installing dependencies is explicitly opt-in for a market install.  The
    # automatic updater never follows a market dependency graph; it only
    # updates the managed item selected by the server owner.
    install_dependencies: bool = Query(
        default=False, description="Whether to install dependencies"
    ),
    acknowledge_warning_rule_ids: list[int] = Query(
        default=[], description="Current soft-conflict rule IDs explicitly acknowledged"
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
    selected_release_id, selected_release_tag, selected_asset_name = _requested_release(
        download_url
    )
    linux_runtime_profile = None

    # Get plugin and server (read-only, no locking)
    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if not plugin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    # Verify server ownership and keep the detached connection settings for install work.
    server = await get_server_for_user(server_id, db, current_user)

    # The same server-side planner is used by the web UI and AI tools. Hard
    # conflicts cannot be overridden; warning acknowledgements are rule-ID
    # specific and therefore become stale as soon as an administrator changes
    # the rules.
    try:
        install_plan = await build_plugin_install_plan(
            db,
            server_id,
            plugin_id,
            include_dependencies=install_dependencies,
            server=server,
        )
        validate_plugin_plan_acknowledgements(install_plan, acknowledge_warning_rule_ids)
    except PluginPlanError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    # Validate SSH before mutating plugin metadata.
    ssh_success, ssh_msg = await _check_plugin_ssh(server)
    if not ssh_success:
        return GitHubPluginInstallResponse(
            success=False,
            message=f"Cannot connect to server via SSH: {ssh_msg}. Please check server connectivity before installing plugins.",
        )

    (
        download_url,
        selected_release_id,
        selected_release_tag,
        selected_asset_name,
        resolve_error,
        linux_runtime_profile,
    ) = await _resolve_market_asset(
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
        await _install_dependencies(
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

    plan_error = await _validate_latest_target_plan(
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

    return await _execute_market_install(
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
    # Get plugins with optional search
    plugins, _ = await MarketPlugin.search_plugins(
        db,
        search_query=search,
        skip=0,
        limit=100,  # Reduced limit since we now support search
    )

    # Filter and map to minimal format
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

    # Get plugin
    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if not plugin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    # Verify server ownership and keep proxy settings for the release lookup.
    server = await get_server_for_user(server_id, db, current_user)

    # If download_url is not provided, fetch latest release
    if not download_url:
        try:
            owner, repo = parse_github_url(plugin.github_url)

            # Get latest release
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
            headers = {"Accept": "application/vnd.github+json", "User-Agent": "CS2-ServerManager"}

            github_token = await get_effective_github_token(db, current_user)

            success, data, error = await http_helper.get(
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

            # Find suitable asset
            assets = data.get("assets", [])
            for asset in assets if isinstance(assets, list) else []:
                if not isinstance(asset, dict):
                    continue
                asset_name = str(asset.get("name") or "").lower()

                # Skip Windows assets
                if (
                    "windows" in asset_name
                    or "-win-" in asset_name
                    or "_win_" in asset_name
                    or asset_name.endswith("-win.zip")
                ):
                    continue

                # Check for archive files
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

    # Use the existing analyze_archive function
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
    github_token = await get_effective_github_token(db, current_user)
    return await fetch_github_repo_info(github_url, github_token=github_token)


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

    # Get plugin (just to verify it exists)
    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if not plugin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    # Verify server ownership; the uninstall route performs its own lookup as well.
    await get_server_for_user(server_id, db, current_user)

    result = await uninstall_plugin(server_id, request, db, current_user, _operation_server)
    if result.success:
        from modules.models import ManagedPlugin

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
