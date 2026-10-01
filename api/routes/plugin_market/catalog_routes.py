"""Focused route implementation; the original module retains patchable dependencies."""

from __future__ import annotations

from typing import Optional

from fastapi import Query

from api.dependencies import ActiveUser, AdminUser, DatabaseSession
from modules import (
    ActionResponse,
    MarketPluginCreate,
    MarketPluginListResponse,
    MarketPluginResponse,
    MarketPluginUpdate,
)
from services.compat import LateBoundModule

host = LateBoundModule("api.routes.plugin_market.routes")
router = host.router


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
            category_enum = host.PluginCategory(category)
        except ValueError:
            raise host.HTTPException(
                status_code=host.status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Invalid category. Valid categories: "
                    + ", ".join([c.value for c in host.PluginCategory])
                ),
            ) from None

    framework_enum = None
    if framework:
        try:
            framework_enum = host.host.parse_framework(framework)
        except ValueError as exc:
            raise host.HTTPException(
                status_code=host.status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    skip = (page - 1) * page_size
    plugins, total = await host.MarketPlugin.search_plugins(
        db,
        category=category_enum,
        search_query=search,
        skip=skip,
        limit=page_size,
        framework=framework_enum,
    )
    total_pages = (total + page_size - 1) // page_size
    plugin_responses = await host.host.populate_dependency_details(db, plugins)
    return host.MarketPluginListResponse(
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
    plugin = await host.MarketPlugin.get_by_id(db, plugin_id)
    if not plugin:
        raise host.HTTPException(
            status_code=host.status.HTTP_404_NOT_FOUND, detail="Plugin not found"
        )
    plugin_responses = await host.host.populate_dependency_details(db, [plugin])
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
    existing = await host.MarketPlugin.get_by_github_url(db, request.github_url)
    if existing:
        raise host.HTTPException(
            status_code=host.status.HTTP_409_CONFLICT,
            detail="Plugin with this GitHub URL already exists",
        )

    title = request.title
    description = request.description
    author = request.author

    if not title or not description:
        github_token = await host.host.get_effective_github_token(db, current_user)
        await db.commit()
        repo_info = await host.host.fetch_github_repo_info(
            request.github_url, github_token=github_token
        )
        if repo_info.success:
            if not title and repo_info.repo_name:
                title = repo_info.repo_name
            if not description and repo_info.description:
                description = repo_info.description
            if not author and repo_info.author:
                author = repo_info.author

    try:
        category_enum = host.PluginCategory(request.category)
    except ValueError:
        raise host.HTTPException(
            status_code=host.status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid category. Valid categories: "
                + ", ".join([c.value for c in host.PluginCategory])
            ),
        ) from None

    try:
        framework_enum = host.host.parse_framework(request.framework)
    except ValueError as exc:
        raise host.HTTPException(
            status_code=host.status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    if request.dependencies:
        try:
            dep_ids = host.host.parse_dependency_ids(request.dependencies)
            await host.host.validate_dependencies(db, dep_ids)
        except ValueError as e:
            raise host.HTTPException(
                status_code=host.status.HTTP_400_BAD_REQUEST, detail=str(e)
            ) from e

    plugin = host.MarketPlugin(
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
    host.logger.info("Plugin '%s' added to market by admin %s", plugin.title, current_user.username)
    return host.MarketPluginResponse.model_validate(plugin)


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
    plugin = await host.MarketPlugin.get_by_id(db, plugin_id)
    if not plugin:
        raise host.HTTPException(
            status_code=host.status.HTTP_404_NOT_FOUND, detail="Plugin not found"
        )

    if request.dependencies:
        try:
            dep_ids = host.host.parse_dependency_ids(request.dependencies)
            await host.host.validate_dependencies(db, dep_ids)
        except ValueError as e:
            raise host.HTTPException(
                status_code=host.status.HTTP_400_BAD_REQUEST, detail=str(e)
            ) from e

    try:
        host.apply_market_plugin_update(plugin, request)
    except ValueError as exc:
        raise host.HTTPException(
            status_code=host.status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    db.add(plugin)
    await db.commit()
    await db.refresh(plugin)
    host.logger.info("Plugin '%s' updated by admin %s", plugin.title, current_user.username)
    return host.MarketPluginResponse.model_validate(plugin)


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
    plugin = await host.host.delete_market_plugin(db, plugin_id)
    if not plugin:
        raise host.HTTPException(
            status_code=host.status.HTTP_404_NOT_FOUND, detail="Plugin not found"
        )
    host.logger.info("Plugin '%s' deleted by admin %s", plugin.title, current_user.username)
    return host.ActionResponse(
        success=True, message=f"Plugin '{plugin.title}' deleted successfully"
    )


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

    plugin = await host.MarketPlugin.get_by_id(db, plugin_id)
    if not plugin:
        raise host.HTTPException(
            status_code=host.status.HTTP_404_NOT_FOUND, detail="Plugin not found"
        )
    return await get_github_releases(
        repo_url=plugin.github_url,
        count=count,
        server_id=server_id,
        db=db,
        current_user=current_user,
    )
