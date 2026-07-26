"""Configuration API for per-server managed plugin automatic updates."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from modules import (
    ActionResponse,
    ManagedPlugin,
    ManagedPluginCreate,
    ManagedPluginResponse,
    ManagedPluginUpdate,
    MarketPlugin,
    PluginAutoUpdateResponse,
    PluginAutoUpdateSettings,
    Server,
    User,
    get_current_active_user,
    get_db,
)
from services.maintenance_lock import maintenance_lock_service
from services.plugin_auto_update_service import (
    FRAMEWORKS,
    canonical_repo_url,
    plugin_auto_update_service,
)
from services.task_registry import plugin_update_task_registry

router = APIRouter(
    prefix="/api/servers/{server_id}/plugin-auto-update", tags=["plugin-auto-update"]
)
_background_tasks = plugin_update_task_registry.tasks
logger = logging.getLogger(__name__)


def _task_done(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        logger.error("Manual plugin update check failed: %s", task.exception())


async def shutdown_background_tasks() -> None:
    """Compatibility wrapper for lifecycle-owned task cleanup."""
    await plugin_update_task_registry.shutdown()


async def owned_server(db: AsyncSession, server_id: int, user: User) -> Server:
    server = (
        await Server.get_by_id(db, server_id)
        if user.is_admin
        else await Server.get_by_id_and_user(db, server_id, user.id)
    )
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    return server


async def owned_plugin(db: AsyncSession, server_id: int, plugin_id: int) -> ManagedPlugin:
    result = await db.execute(
        select(ManagedPlugin).where(
            ManagedPlugin.id == plugin_id, ManagedPlugin.server_id == server_id
        )
    )
    plugin = result.scalar_one_or_none()
    if not plugin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Managed plugin not found"
        )
    return plugin


@router.get("", response_model=PluginAutoUpdateResponse)
async def get_configuration(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    server = await owned_server(db, server_id, current_user)
    result = await db.execute(
        select(ManagedPlugin)
        .where(ManagedPlugin.server_id == server_id)
        .order_by(ManagedPlugin.display_name)
    )
    return PluginAutoUpdateResponse(
        enable_plugin_auto_update=server.enable_plugin_auto_update,
        plugin_update_check_interval_hours=server.plugin_update_check_interval_hours,
        last_plugin_update_check=server.last_plugin_update_check,
        plugins=[ManagedPluginResponse.model_validate(item) for item in result.scalars().all()],
    )


@router.put("/settings", response_model=PluginAutoUpdateResponse)
async def update_settings(
    server_id: int,
    request: PluginAutoUpdateSettings,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    server = await owned_server(db, server_id, current_user)
    server.enable_plugin_auto_update = request.enable_plugin_auto_update
    server.plugin_update_check_interval_hours = request.plugin_update_check_interval_hours
    db.add(server)
    await db.commit()
    await db.refresh(server)
    return await get_configuration(server_id, db, current_user)


@router.post("/plugins", response_model=ManagedPluginResponse, status_code=status.HTTP_201_CREATED)
async def register_plugin(
    server_id: int,
    request: ManagedPluginCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await owned_server(db, server_id, current_user)
    repo_url = canonical_repo_url(request.repo_url) if request.repo_url else None
    source_key = request.source_key
    display_name = request.display_name
    framework_key = request.framework_key
    asset_glob = request.asset_glob
    custom_install_path = request.custom_install_path
    if request.source_type == "framework":
        if framework_key not in FRAMEWORKS:
            raise HTTPException(status_code=400, detail="Unsupported framework")
        framework = FRAMEWORKS[framework_key]
        source_key, repo_url = framework_key, framework["repo_url"]
        display_name, asset_glob = framework["name"], framework["asset_glob"]
    elif request.source_type == "market":
        if not request.market_plugin_id:
            raise HTTPException(status_code=400, detail="market_plugin_id is required")
        market_plugin = await db.get(MarketPlugin, request.market_plugin_id)
        if not market_plugin:
            raise HTTPException(status_code=404, detail="Market plugin not found")
        source_key = str(market_plugin.id)
        repo_url = canonical_repo_url(market_plugin.github_url)
        display_name = market_plugin.title
        asset_glob = request.asset_glob
        custom_install_path = request.custom_install_path or market_plugin.custom_install_path
        if not asset_glob:
            raise HTTPException(status_code=400, detail="asset_glob is required for market plugins")
    elif not repo_url or not asset_glob:
        raise HTTPException(
            status_code=400, detail="repo_url and asset_glob are required for GitHub plugins"
        )
    source_key = source_key or (repo_url.lower() if repo_url else None)
    existing = await db.execute(
        select(ManagedPlugin).where(
            ManagedPlugin.server_id == server_id,
            ManagedPlugin.source_type == request.source_type,
            ManagedPlugin.source_key == source_key,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Plugin is already managed"
        )
    plugin = ManagedPlugin(
        server_id=server_id,
        source_type=request.source_type,
        source_key=source_key,
        display_name=display_name,
        repo_url=repo_url,
        market_plugin_id=request.market_plugin_id,
        framework_key=framework_key,
        installed_release_id=request.installed_release_id,
        installed_version=request.installed_version or "unknown",
        asset_glob=asset_glob,
        custom_install_path=custom_install_path,
        exclude_dirs=request.exclude_dirs,
        exclude_files=request.exclude_files,
        auto_update_enabled=request.auto_update_enabled,
        backup_before_update=request.backup_before_update,
        restart_after_update=request.restart_after_update,
    )
    db.add(plugin)
    await db.commit()
    await db.refresh(plugin)
    return ManagedPluginResponse.model_validate(plugin)


@router.patch("/plugins/{plugin_id}", response_model=ManagedPluginResponse)
async def update_plugin(
    server_id: int,
    plugin_id: int,
    request: ManagedPluginUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await owned_server(db, server_id, current_user)
    plugin = await owned_plugin(db, server_id, plugin_id)
    plugin.sqlmodel_update(request.model_dump(exclude_unset=True))
    db.add(plugin)
    await db.commit()
    await db.refresh(plugin)
    return ManagedPluginResponse.model_validate(plugin)


@router.delete("/plugins/{plugin_id}", response_model=ActionResponse)
async def unmanage_plugin(
    server_id: int,
    plugin_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await owned_server(db, server_id, current_user)
    plugin = await owned_plugin(db, server_id, plugin_id)
    await db.delete(plugin)
    await db.commit()
    return ActionResponse(
        success=True, message="Plugin is no longer managed; remote files were not removed"
    )


@router.post("/run", response_model=ActionResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_now(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await owned_server(db, server_id, current_user)
    if await maintenance_lock_service.is_locked(server_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another maintenance operation is already running",
        )
    task = asyncio.create_task(plugin_auto_update_service.check_server(server_id, force=True))
    plugin_update_task_registry.add(
        task,
        on_error=lambda completed, _error: _task_done(completed),
    )
    return ActionResponse(success=True, message="Plugin update check started")


@router.post(
    "/plugins/{plugin_id}/test-update",
    response_model=ActionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def test_plugin_update(
    server_id: int,
    plugin_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Run the normal protected update pipeline for one managed plugin."""
    await owned_server(db, server_id, current_user)
    await owned_plugin(db, server_id, plugin_id)
    if await maintenance_lock_service.is_locked(server_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another maintenance operation is already running",
        )
    task = asyncio.create_task(plugin_auto_update_service.check_plugin(server_id, plugin_id))
    plugin_update_task_registry.add(
        task,
        on_error=lambda completed, _error: _task_done(completed),
    )
    return ActionResponse(success=True, message="Plugin test update started")


@router.get("/status")
async def get_run_status(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await owned_server(db, server_id, current_user)
    return await plugin_auto_update_service.get_status(server_id)
