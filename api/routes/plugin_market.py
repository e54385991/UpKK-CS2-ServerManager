"""
Plugin Market routes
Provides endpoints for browsing, searching, and installing plugins from the market
"""

import logging
import re
from typing import Any, List, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.dependencies import get_ssh_manager, locked_server_operation
from api.http_resource import (
    ApplicationHTTP,
    as_application_http,
    resolve_application_http,
)
from cs2_manager.core import ErrorResponse
from modules import (
    ActionResponse,
    ArchiveAnalysisResponse,
    DependencyInfo,
    GitHubPluginInstallRequest,
    GitHubPluginInstallResponse,
    GitHubReleasesResponse,
    GitHubRepoInfo,
    MarketPlugin,
    MarketPluginCreate,
    MarketPluginListResponse,
    MarketPluginResponse,
    MarketPluginUpdate,
    PluginCategory,
    PluginUninstallRequest,
    Server,
    User,
    get_current_active_user,
    get_current_admin_user,
    get_db,
)
from modules.http_helper import http_helper
from services import SSHManager
from services.github_credentials import get_effective_github_token
from services.plugin_installation import install_github_plugin

router = APIRouter(prefix="/api/plugin-market", tags=["plugin-market"])

logger = logging.getLogger(__name__)

OUTBOUND_HTTP_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
}

# Regex to validate GitHub repository URL (supports both https and git formats)
GITHUB_REPO_PATTERN = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+?)(?:\.git)?(?:/.*)?$"
)


def _coerce_ssh_manager(candidate: object) -> SSHManager:
    """Preserve direct-call compatibility while ASGI requests inject a manager."""
    if callable(getattr(candidate, "disconnect", None)):
        return candidate  # type: ignore[return-value]
    return SSHManager()


async def get_server_for_user(server_id: int, db: AsyncSession, current_user: User) -> Server:
    """Helper to get server and verify ownership - admins can access any server"""
    if current_user.is_admin:
        server = await Server.get_by_id(db, server_id)
    else:
        server = await Server.get_by_id_and_user(db, server_id, current_user.id)

    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    snapshot = Server.model_validate(server, from_attributes=True)
    await db.commit()
    return snapshot


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


async def validate_dependencies(db: AsyncSession, dependency_ids: list[int]) -> None:
    """
    Validate that all dependency plugin IDs exist in the database.

    Args:
        db: Database session
        dependency_ids: List of plugin IDs to validate

    Raises:
        HTTPException: If any dependency plugin is not found
    """
    for dep_id in dependency_ids:
        dep_plugin = await MarketPlugin.get_by_id(db, dep_id)
        if not dep_plugin:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Dependency plugin with ID {dep_id} not found",
            )


async def fetch_github_repo_info(
    github_url: str,
    github_proxy: Optional[str] = None,
    github_token: Optional[str] = None,
    *,
    http_resource: ApplicationHTTP | None = None,
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

    outbound_http: ApplicationHTTP = http_resource or http_helper
    success, data, error = await outbound_http.get(
        api_url, headers=headers, timeout=30, proxy=github_proxy, github_token=github_token
    )

    if not success:
        return GitHubRepoInfo(success=False, error=f"Failed to fetch repository info: {error}")

    # Extract repo name and description
    repo_name = data.get("name", repo)
    description = data.get("description", "")

    # Fetch README to get first 200 characters
    readme_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
    readme_success, readme_data, _ = await outbound_http.get(
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
    dependency_ids: set[int] = set()
    parsed_dependencies: dict[int, list[int]] = {}

    for index, plugin in enumerate(plugins):
        if not plugin.dependencies:
            continue
        try:
            plugin_dependency_ids = parse_dependency_ids(plugin.dependencies)
        except ValueError:
            # Preserve the existing behavior for malformed legacy data: expose
            # the plugin but omit dependency details.
            continue
        parsed_dependencies[index] = plugin_dependency_ids
        dependency_ids.update(plugin_dependency_ids)

    dependencies_by_id: dict[int, MarketPlugin] = {}
    if dependency_ids:
        result = await db.execute(select(MarketPlugin).where(MarketPlugin.id.in_(dependency_ids)))
        dependencies_by_id = {
            dependency.id: dependency
            for dependency in result.scalars().all()
            if dependency.id is not None
        }

    responses = []

    for index, plugin in enumerate(plugins):
        response = MarketPluginResponse.model_validate(plugin)

        # Populate dependency details if plugin has dependencies
        dependency_details = [
            DependencyInfo(id=dependency.id, title=dependency.title)
            for dependency_id in parsed_dependencies.get(index, ())
            if (dependency := dependencies_by_id.get(dependency_id)) is not None
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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


@router.post(
    "/plugins",
    response_model=MarketPluginResponse,
    status_code=status.HTTP_200_OK,
    responses={
        **OUTBOUND_HTTP_ERROR_RESPONSES,
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    },
)
async def create_plugin(
    request: MarketPluginCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
    http_resource: ApplicationHTTP | object = Depends(resolve_application_http),
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
        await db.commit()
        repo_info = await fetch_github_repo_info(
            request.github_url,
            github_token=github_token,
            http_resource=cast(ApplicationHTTP, http_resource),
        )
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
) -> ActionResponse:
    """
    Delete a plugin from the market (admin only).

    Args:
        plugin_id: Plugin ID

    Returns:
        Success response
    """
    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if not plugin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    plugin_title = plugin.title
    await db.delete(plugin)
    await db.commit()

    logger.info(f"Plugin '{plugin_title}' deleted by admin {current_user.username}")

    return ActionResponse(success=True, message=f"Plugin '{plugin_title}' deleted successfully")


@router.get(
    "/plugins/{plugin_id}/releases",
    response_model=GitHubReleasesResponse,
    status_code=status.HTTP_200_OK,
    responses=OUTBOUND_HTTP_ERROR_RESPONSES,
)
async def get_plugin_releases(
    plugin_id: int,
    server_id: Optional[int] = Query(None, description="Optional server ID for GitHub proxy"),
    count: int = Query(5, ge=1, le=10, description="Number of releases to fetch"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    http_resource: ApplicationHTTP | object = Depends(resolve_application_http),
) -> GitHubReleasesResponse:
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
        http_resource=cast(ApplicationHTTP, http_resource),
    )


# Common configuration file extensions to exclude during upgrade mode
# These files are typically user-configured and should be preserved
CONFIG_FILE_EXTENSIONS = [
    # 最常見的核心配置格式（幾乎每個項目都會用到）
    ".ini",  # Windows 傳統、很多老專案、Python configparser
    ".cfg",  # 通用配置（遊戲、伺服器、軟體常見）
    ".conf",  # Linux/Unix 系統服務最愛（nginx.conf, apache2.conf）
    ".config",  # 一些框架/工具的偏好（.gitconfig 其實是 .git/config）
    ".json",  # 前端、後端 API、Node.js、VS Code settings
    ".jsonc",  # JSON with Comments（VS Code、TypeScript 常用）
    ".json_c",  # JSON with Comments（VS Code、TypeScript 常用）
    ".json5",  # JSON5（支援註解、尾隨逗號、無引號 key）
    ".yaml",  # DevOps 王者（Kubernetes、Docker Compose、GitHub Actions、Ansible）
    ".yml",  # YAML 的最常見縮寫形式
    ".toml",  # Python (pyproject.toml)、Rust (Cargo.toml)、現代新寵
    ".env",  # 環境變數（dotenv 最經典，幾乎所有後端框架都支援）
    # 傳統/企業/特定生態系
    ".xml",  # Java 生態、老企業系統、Maven pom.xml、Spring
    ".properties",  # Java Properties 格式（.properties / application.properties）
    ".prop",  # 少見但有些專案用
    ".setting",  # 某些軟體的設定檔
    ".settings",  # 多數情況是資料夾，但有些是 .settings 檔
    # 特定語言/工具專屬或高度相關
    ".hcl",  # HashiCorp 配置語言（Terraform .tf 其實是 HCL，但有時單獨 .hcl）
    ".tf",  # Terraform 配置（雖然不是純副檔名，但常被當配置掃描）
    ".tfvars",  # Terraform 變數檔
    ".php",  # WordPress wp-config.php、Laravel config/*.php
    ".py",  # Python 有時直接用 .py 當配置（settings.py）
    ".js",  # Next.js / Nuxt config、雖然不推薦但常見 .config.js
    ".cson",  # CoffeeScript Object Notation（Atom 編輯器用過）
    ".plist",  # macOS / iOS 偏好設定（Info.plist、.plist）
    # 備份、臨時、使用者覆蓋類
    ".bak",  # 備份配置（常見於手動修改前）
    ".old",  # 同上
    ".example",  # 範例配置（.env.example、config.yaml.example）
    ".dist",  # 分發用範例（config.dist.json）
    ".sample",  # 同上
    ".local",  # 個人本地覆蓋（settings.local.json）
    ".user",  # 使用者特定設定
    ".override",  # 有些框架用來覆蓋預設
    # 其他偶爾出現但真實存在的
    ".md",  # 極少，但有些人把配置寫在 markdown 裡（不推薦）
    ".yaml.tpl",  # Helm chart 的模板
    ".j2",  # Ansible Jinja2 模板（雖然是模板但常被掃描）
    ".envrc",  # direnv 工具用的本地環境變數
    ".secrets",  # 有時用來放機密（不安全，但存在）
    ".secret",
]


@router.post(
    "/plugins/{plugin_id}/install",
    response_model=GitHubPluginInstallResponse,
    status_code=status.HTTP_200_OK,
    responses={
        **OUTBOUND_HTTP_ERROR_RESPONSES,
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    },
)
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
    upgrade_mode: bool = Query(
        default=False, description="Enable upgrade mode to auto-exclude config files"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _operation_server: Server = Depends(locked_server_operation),
    http_resource: ApplicationHTTP | object = Depends(resolve_application_http),
    ssh_manager: SSHManager = Depends(get_ssh_manager),
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
    selected_release_id = None
    selected_release_tag = None
    selected_asset_name = None
    outbound_http = as_application_http(http_resource) or http_helper

    # Validate download_url if provided
    if download_url:
        # Ensure it's a GitHub releases download URL
        if (
            not download_url.startswith("https://github.com/")
            or "/releases/download/" not in download_url
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid download URL. Must be a GitHub releases download URL.",
            )
        release_parts = download_url.split("/releases/download/", 1)[1].split("/", 1)
        if len(release_parts) == 2:
            selected_release_tag = release_parts[0]
            selected_release_id = f"tag:{selected_release_tag}"
            selected_asset_name = release_parts[1]

    # Get plugin and server (read-only, no locking)
    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if not plugin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    # Verify server ownership and keep the detached connection settings for install work.
    server = await get_server_for_user(server_id, db, current_user)

    # CRITICAL: Check SSH connectivity BEFORE any database modifications
    # This prevents database locks when SSH connection hangs or fails
    ssh_manager = _coerce_ssh_manager(ssh_manager)
    ssh_success, ssh_msg = await ssh_manager.connect(server)
    await ssh_manager.disconnect()

    if not ssh_success:
        return GitHubPluginInstallResponse(
            success=False,
            message=f"Cannot connect to server via SSH: {ssh_msg}. Please check server connectivity before installing plugins.",
        )

    # Install dependencies first if requested and present
    installed_deps = []
    if install_dependencies and plugin.dependencies:
        try:
            dep_ids = parse_dependency_ids(plugin.dependencies)
            for dep_id in dep_ids:
                dep_plugin = await MarketPlugin.get_by_id(db, dep_id)
                if dep_plugin:
                    logger.info(f"Installing dependency: {dep_plugin.title}")
                    # Recursively install dependency (without its own dependencies to avoid infinite loops)
                    # Pass upgrade_mode to protect config files in dependencies too
                    dep_result = await install_plugin(
                        dep_id,
                        server_id,
                        download_url=None,  # Always use latest version for dependencies to avoid version conflicts
                        exclude_dirs=exclude_dirs,
                        exclude_files=exclude_files,
                        install_dependencies=False,  # Don't recursively install dependencies of dependencies
                        upgrade_mode=upgrade_mode,  # Preserve config files in dependencies when upgrading
                        db=db,
                        current_user=current_user,
                        http_resource=outbound_http,
                        ssh_manager=ssh_manager,
                    )
                    if dep_result.success:
                        installed_deps.append(dep_plugin.title)
                    else:
                        logger.warning(
                            f"Failed to install dependency {dep_plugin.title}: {dep_result.message}"
                        )
        except ValueError as e:
            logger.error(f"Error parsing dependencies: {e}")

    # Increment download count in a separate short transaction to avoid locks
    try:
        plugin.download_count += 1
        db.add(plugin)
        await db.commit()
    except Exception as e:
        # Log but don't fail the installation if download count update fails
        logger.error(f"Failed to update download count: {e}")
        await db.rollback()

    # Refresh plugin to avoid stale data
    await db.refresh(plugin)

    try:
        # If download_url is not provided, fetch latest release from GitHub
        if not download_url:
            # Fetch releases from GitHub (use local parse_github_url function)
            owner, repo = parse_github_url(plugin.github_url)

            # Get latest release
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
            headers = {"Accept": "application/vnd.github+json", "User-Agent": "CS2-ServerManager"}

            github_token = await get_effective_github_token(db, current_user)
            await db.commit()

            success, data, error = await outbound_http.get(
                api_url,
                headers=headers,
                timeout=30,
                proxy=server.github_proxy,
                github_token=github_token,
            )

            if not success:
                message = f"Failed to fetch latest release: {error}"
                if installed_deps:
                    message += f" (Dependencies installed: {', '.join(installed_deps)})"
                return GitHubPluginInstallResponse(success=False, message=message)

            # Find suitable asset (exclude Windows, prefer Linux archives)
            assets = data.get("assets", [])
            selected_release_id = str(data.get("id") or "")
            selected_release_tag = data.get("tag_name") or "unknown"
            download_url = None

            for asset in assets:
                asset_name = asset.get("name", "").lower()

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
                    download_url = asset.get("browser_download_url")
                    selected_asset_name = asset.get("name")
                    break

            if not download_url:
                message = "No suitable release asset found for installation"
                if installed_deps:
                    message += f" (Dependencies installed: {', '.join(installed_deps)})"
                return GitHubPluginInstallResponse(success=False, message=message)

        # Use existing installation logic
        # If upgrade_mode is enabled, add config file extension patterns to exclude_files
        final_exclude_files = list(exclude_files)  # Make a copy
        if upgrade_mode:
            # Add wildcard patterns for common config file extensions
            for ext in CONFIG_FILE_EXTENSIONS:
                # Add pattern that matches files with this extension anywhere in the archive
                final_exclude_files.append(f"*{ext}")
            logger.info(
                f"Upgrade mode enabled: auto-excluding config files with extensions {CONFIG_FILE_EXTENSIONS}"
            )

        install_request = GitHubPluginInstallRequest(
            download_url=download_url,
            exclude_dirs=exclude_dirs,
            exclude_files=final_exclude_files,
            custom_install_path=plugin.custom_install_path,
        )

        result = await install_github_plugin(
            server_id,
            install_request,
            db,
            current_user,
            ssh_manager=ssh_manager,
            http_resource=outbound_http,
        )

        # Increment install count if successful (separate transaction)
        if result.success:
            try:
                plugin.install_count += 1
                db.add(plugin)
                await db.commit()
            except Exception as e:
                # Log but don't fail if install count update fails
                logger.error(f"Failed to update install count: {e}")
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
                asset_glob=derive_asset_glob(selected_asset_name, selected_release_tag),
                custom_install_path=plugin.custom_install_path,
                exclude_dirs=exclude_dirs,
                exclude_files=final_exclude_files,
            )

            # Add dependency info to success message
            if installed_deps:
                result.message += f" (Dependencies also installed: {', '.join(installed_deps)})"

        return result

    except Exception as e:
        logger.error(f"Error installing plugin: {e}", exc_info=True)
        message = f"Installation error: {str(e)}"
        if installed_deps:
            message += f" (Dependencies installed: {', '.join(installed_deps)})"
        return GitHubPluginInstallResponse(success=False, message=message)


@router.get("/categories")
async def list_categories(current_user: User = Depends(get_current_active_user)) -> dict:
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
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


@router.get(
    "/plugins/{plugin_id}/analyze-archive",
    response_model=ArchiveAnalysisResponse,
    status_code=status.HTTP_200_OK,
    responses={
        **OUTBOUND_HTTP_ERROR_RESPONSES,
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
async def analyze_plugin_archive(
    plugin_id: int,
    server_id: int = Query(..., description="Server ID for analysis"),
    download_url: Optional[str] = Query(
        None, description="Specific release download URL (if not provided, uses latest)"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    http_resource: ApplicationHTTP | object = Depends(resolve_application_http),
) -> ArchiveAnalysisResponse:
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
            await db.commit()

            success, data, error = await cast(ApplicationHTTP, http_resource).get(
                api_url,
                headers=headers,
                timeout=30,
                proxy=server.github_proxy,
                github_token=github_token,
            )

            if not success:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to fetch latest release: {error}",
                )

            # Find suitable asset
            assets = data.get("assets", [])
            for asset in assets:
                asset_name = asset.get("name", "").lower()

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
                    download_url = asset.get("browser_download_url")
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


@router.post(
    "/fetch-repo-info",
    response_model=GitHubRepoInfo,
    status_code=status.HTTP_200_OK,
    responses=OUTBOUND_HTTP_ERROR_RESPONSES,
)
async def fetch_repo_info(
    github_url: str = Query(..., description="GitHub repository URL"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
    http_resource: ApplicationHTTP | object = Depends(resolve_application_http),
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
    await db.commit()
    return await fetch_github_repo_info(
        github_url,
        github_token=github_token,
        http_resource=cast(ApplicationHTTP, http_resource),
    )


@router.post("/plugins/{plugin_id}/uninstall")
async def uninstall_market_plugin(
    plugin_id: int,
    server_id: int,
    request: PluginUninstallRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _operation_server: Server = Depends(locked_server_operation),
    ssh_manager: SSHManager = Depends(get_ssh_manager),
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

    result = await uninstall_plugin(
        server_id,
        request,
        db,
        current_user,
        ssh_manager=ssh_manager,
    )
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
