"""
Plugin Market routes
Provides endpoints for browsing, searching, and installing plugins from the market
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
import re
import logging

from modules import (
    MarketPlugin, PluginCategory, get_db, User,
    get_current_active_user, get_current_admin_user,
    MarketPluginCreate, MarketPluginUpdate, MarketPluginResponse,
    MarketPluginListResponse, GitHubRepoInfo, ActionResponse,
    Server, GitHubPluginInstallRequest, GitHubPluginInstallResponse,
    PluginUninstallRequest,
    DependencyInfo
)
from modules.http_helper import http_helper

router = APIRouter(prefix="/api/plugin-market", tags=["plugin-market"])

logger = logging.getLogger(__name__)

# Regex to validate GitHub repository URL (supports both https and git formats)
GITHUB_REPO_PATTERN = re.compile(
    r'^(?:https://github\.com/|git@github\.com:)([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+?)(?:\.git)?(?:/.*)?$'
)


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
    for dep in dependencies.split(','):
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
                detail=f"Dependency plugin with ID {dep_id} not found"
            )


async def fetch_github_repo_info(github_url: str, github_proxy: Optional[str] = None) -> GitHubRepoInfo:
    """
    Fetch repository information from GitHub API.
    
    Args:
        github_url: GitHub repository URL
        github_proxy: Optional GitHub proxy URL
    
    Returns:
        GitHubRepoInfo with parsed data
    """
    try:
        owner, repo = parse_github_url(github_url)
    except ValueError as e:
        return GitHubRepoInfo(
            success=False,
            error=str(e)
        )
    
    # Fetch repo info from GitHub API
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "CS2-ServerManager"
    }
    
    success, data, error = await http_helper.get(
        api_url,
        headers=headers,
        timeout=30,
        proxy=github_proxy
    )
    
    if not success:
        return GitHubRepoInfo(
            success=False,
            error=f"Failed to fetch repository info: {error}"
        )
    
    # Extract repo name and description
    repo_name = data.get("name", repo)
    description = data.get("description", "")
    
    # Fetch README to get first 200 characters
    readme_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
    readme_success, readme_data, _ = await http_helper.get(
        readme_url,
        headers=headers,
        timeout=30,
        proxy=github_proxy
    )
    
    if readme_success and isinstance(readme_data, dict):
        # GitHub API returns base64-encoded content
        import base64
        content = readme_data.get("content", "")
        if content:
            try:
                decoded = base64.b64decode(content).decode('utf-8')
                # Remove markdown headers and extract first 200 chars
                lines = [line.strip() for line in decoded.split('\n') if line.strip() and not line.strip().startswith('#')]
                if lines:
                    description = ' '.join(lines)[:200]
            except Exception as e:
                logger.warning(f"Failed to decode README: {e}")
    
    return GitHubRepoInfo(
        success=True,
        repo_name=repo_name,
        description=description if description else None,
        author=owner
    )


async def populate_dependency_details(
    db: AsyncSession, 
    plugins: List[MarketPlugin]
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
    
    for plugin in plugins:
        response = MarketPluginResponse.model_validate(plugin)
        
        # Populate dependency details if plugin has dependencies
        if plugin.dependencies:
            try:
                dep_ids = parse_dependency_ids(plugin.dependencies)
                dependency_details = []
                
                for dep_id in dep_ids:
                    dep_plugin = await MarketPlugin.get_by_id(db, dep_id)
                    if dep_plugin:
                        dependency_details.append(DependencyInfo(
                            id=dep_plugin.id,
                            title=dep_plugin.title
                        ))
                
                response.dependency_details = dependency_details if dependency_details else None
            except ValueError:
                # Invalid dependency format, skip
                pass
        
        responses.append(response)
    
    return responses


@router.get("/plugins", response_model=MarketPluginListResponse)
async def list_plugins(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    category: Optional[str] = Query(None, description="Filter by category"),
    search: Optional[str] = Query(None, description="Search query"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
                detail=f"Invalid category. Valid categories: {', '.join([c.value for c in PluginCategory])}"
            )
    
    # Calculate skip
    skip = (page - 1) * page_size
    
    # Search plugins
    plugins, total = await MarketPlugin.search_plugins(
        db,
        category=category_enum,
        search_query=search,
        skip=skip,
        limit=page_size
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
        total_pages=total_pages
    )


@router.get("/plugins/{plugin_id}", response_model=MarketPluginResponse)
async def get_plugin(
    plugin_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plugin not found"
        )
    
    # Populate dependency details
    plugin_responses = await populate_dependency_details(db, [plugin])
    return plugin_responses[0]


@router.post("/plugins", response_model=MarketPluginResponse)
async def create_plugin(
    request: MarketPluginCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
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
            detail="Plugin with this GitHub URL already exists"
        )
    
    # Auto-fetch repo info if title or description not provided
    title = request.title
    description = request.description
    author = request.author
    
    if not title or not description:
        repo_info = await fetch_github_repo_info(request.github_url)
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
            detail=f"Invalid category. Valid categories: {', '.join([c.value for c in PluginCategory])}"
        )
    
    # Validate dependencies if provided
    if request.dependencies:
        try:
            dep_ids = parse_dependency_ids(request.dependencies)
            await validate_dependencies(db, dep_ids)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
    
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
        custom_install_path=request.custom_install_path
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
    current_user: User = Depends(get_current_admin_user)
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plugin not found"
        )
    
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
                detail=f"Invalid category. Valid categories: {', '.join([c.value for c in PluginCategory])}"
            )
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
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(e)
                )
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
    current_user: User = Depends(get_current_admin_user)
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plugin not found"
        )
    
    plugin_title = plugin.title
    await db.delete(plugin)
    await db.commit()
    
    logger.info(f"Plugin '{plugin_title}' deleted by admin {current_user.username}")
    
    return ActionResponse(
        success=True,
        message=f"Plugin '{plugin_title}' deleted successfully"
    )


@router.get("/plugins/{plugin_id}/releases")
async def get_plugin_releases(
    plugin_id: int,
    server_id: Optional[int] = Query(None, description="Optional server ID for GitHub proxy"),
    count: int = Query(5, ge=1, le=10, description="Number of releases to fetch"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plugin not found"
        )
    
    # Fetch releases using the existing github_plugins endpoint logic
    return await get_github_releases(
        repo_url=plugin.github_url,
        count=count,
        server_id=server_id,
        db=db,
        current_user=current_user
    )


@router.post("/plugins/{plugin_id}/install", response_model=GitHubPluginInstallResponse)
async def install_plugin(
    plugin_id: int,
    server_id: int = Query(..., description="Server ID to install plugin on"),
    download_url: Optional[str] = Query(None, description="Specific release download URL (if not provided, uses latest)"),
    exclude_dirs: list[str] = Query(default=[], description="Directories to exclude (deprecated, use exclude_files)"),
    exclude_files: list[str] = Query(default=[], description="Files to exclude from installation"),
    install_dependencies: bool = Query(default=True, description="Whether to install dependencies"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
    
    Returns:
        Installation result
    """
    # Validate download_url if provided
    if download_url:
        # Ensure it's a GitHub releases download URL
        if not download_url.startswith('https://github.com/') or '/releases/download/' not in download_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid download URL. Must be a GitHub releases download URL."
            )
    
    # Get plugin and server (read-only, no locking)
    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if not plugin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plugin not found"
        )
    
    # Verify server ownership
    server = await Server.get_by_id_and_user(db, server_id, current_user.id)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found"
        )
    
    # CRITICAL: Check SSH connectivity BEFORE any database modifications
    # This prevents database locks when SSH connection hangs or fails
    from services import SSHManager
    ssh_manager = SSHManager()
    ssh_success, ssh_msg = await ssh_manager.connect(server)
    await ssh_manager.disconnect()
    
    if not ssh_success:
        return GitHubPluginInstallResponse(
            success=False,
            message=f"Cannot connect to server via SSH: {ssh_msg}. Please check server connectivity before installing plugins."
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
                    dep_result = await install_plugin(
                        dep_id, 
                        server_id,
                        download_url=None,  # Always use latest version for dependencies to avoid version conflicts
                        exclude_dirs=exclude_dirs,
                        exclude_files=exclude_files,
                        install_dependencies=False,  # Don't recursively install dependencies of dependencies
                        db=db, 
                        current_user=current_user
                    )
                    if dep_result.success:
                        installed_deps.append(dep_plugin.title)
                    else:
                        logger.warning(f"Failed to install dependency {dep_plugin.title}: {dep_result.message}")
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
            headers = {
                "Accept": "application/vnd.github+json",
                "User-Agent": "CS2-ServerManager"
            }
            
            success, data, error = await http_helper.get(
                api_url,
                headers=headers,
                timeout=30,
                proxy=server.github_proxy
            )
            
            if not success:
                message = f"Failed to fetch latest release: {error}"
                if installed_deps:
                    message += f" (Dependencies installed: {', '.join(installed_deps)})"
                return GitHubPluginInstallResponse(
                    success=False,
                    message=message
                )
            
            # Find suitable asset (exclude Windows, prefer Linux archives)
            assets = data.get("assets", [])
            download_url = None
            
            for asset in assets:
                asset_name = asset.get("name", "").lower()
                
                # Skip Windows assets
                if 'windows' in asset_name or '-win-' in asset_name or '_win_' in asset_name or asset_name.endswith('-win.zip'):
                    continue
                
                # Check for archive files
                if any(asset_name.endswith(ext) for ext in [".zip", ".tar.gz", ".tgz", ".tar", ".7z"]):
                    download_url = asset.get("browser_download_url")
                    break
            
            if not download_url:
                message = "No suitable release asset found for installation"
                if installed_deps:
                    message += f" (Dependencies installed: {', '.join(installed_deps)})"
                return GitHubPluginInstallResponse(
                    success=False,
                    message=message
                )
        
        # Use existing installation logic
        from api.routes.github_plugins import install_github_plugin
        
        install_request = GitHubPluginInstallRequest(
            download_url=download_url,
            exclude_dirs=exclude_dirs,
            exclude_files=exclude_files,
            custom_install_path=plugin.custom_install_path
        )
        
        result = await install_github_plugin(server_id, install_request, db, current_user)
        
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
            
            # Add dependency info to success message
            if installed_deps:
                result.message += f" (Dependencies also installed: {', '.join(installed_deps)})"
        
        return result
        
    except Exception as e:
        logger.error(f"Error installing plugin: {e}", exc_info=True)
        message = f"Installation error: {str(e)}"
        if installed_deps:
            message += f" (Dependencies installed: {', '.join(installed_deps)})"
        return GitHubPluginInstallResponse(
            success=False,
            message=message
        )


@router.get("/categories")
async def list_categories(
    current_user: User = Depends(get_current_active_user)
) -> dict:
    """
    Get list of available plugin categories.
    
    Returns:
        List of category values and names
    """
    categories = [
        {"value": c.value, "name": c.value.replace("_", " ").title()}
        for c in PluginCategory
    ]
    
    return {
        "success": True,
        "categories": categories
    }


@router.get("/plugins-for-dependencies")
async def list_plugins_for_dependencies(
    exclude_id: Optional[int] = Query(None, description="Plugin ID to exclude (for editing)"),
    search: Optional[str] = Query(None, description="Search query for filtering plugins"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
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
        limit=100  # Reduced limit since we now support search
    )
    
    # Filter and map to minimal format
    plugin_list = [
        {"id": p.id, "title": p.title}
        for p in plugins
        if exclude_id is None or p.id != exclude_id
    ]
    
    return {
        "success": True,
        "plugins": plugin_list
    }


@router.get("/plugins/{plugin_id}/analyze-archive")
async def analyze_plugin_archive(
    plugin_id: int,
    server_id: int = Query(..., description="Server ID for analysis"),
    download_url: Optional[str] = Query(None, description="Specific release download URL (if not provided, uses latest)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plugin not found"
        )
    
    # Verify server ownership
    server = await Server.get_by_id_and_user(db, server_id, current_user.id)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found"
        )
    
    # If download_url is not provided, fetch latest release
    if not download_url:
        try:
            owner, repo = parse_github_url(plugin.github_url)
            
            # Get latest release
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
            headers = {
                "Accept": "application/vnd.github+json",
                "User-Agent": "CS2-ServerManager"
            }
            
            success, data, error = await http_helper.get(
                api_url,
                headers=headers,
                timeout=30,
                proxy=server.github_proxy
            )
            
            if not success:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to fetch latest release: {error}"
                )
            
            # Find suitable asset
            assets = data.get("assets", [])
            for asset in assets:
                asset_name = asset.get("name", "").lower()
                
                # Skip Windows assets
                if 'windows' in asset_name or '-win-' in asset_name or '_win_' in asset_name or asset_name.endswith('-win.zip'):
                    continue
                
                # Check for archive files
                if any(asset_name.endswith(ext) for ext in [".zip", ".tar.gz", ".tgz", ".tar", ".7z"]):
                    download_url = asset.get("browser_download_url")
                    break
            
            if not download_url:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No suitable release asset found"
                )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
    
    # Use the existing analyze_archive function
    return await analyze_github_archive(
        server_id=server_id,
        download_url=download_url,
        db=db,
        current_user=current_user
    )


@router.post("/fetch-repo-info", response_model=GitHubRepoInfo)
async def fetch_repo_info(
    github_url: str = Query(..., description="GitHub repository URL"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
) -> GitHubRepoInfo:
    """
    Fetch repository information from GitHub (admin only).
    Helper endpoint for auto-filling plugin details.
    
    Args:
        github_url: GitHub repository URL
    
    Returns:
        Repository information
    """
    return await fetch_github_repo_info(github_url)


@router.post("/plugins/{plugin_id}/uninstall")
async def uninstall_market_plugin(
    plugin_id: int,
    server_id: int,
    request: PluginUninstallRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plugin not found"
        )
    
    # Verify server ownership
    server = await Server.get_by_id_and_user(db, server_id, current_user.id)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found"
        )
    
    # Use the existing uninstall function
    return await uninstall_plugin(server_id, request, db, current_user)
