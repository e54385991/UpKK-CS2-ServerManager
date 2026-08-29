"""Versioned plugin auto-update workspace for one game server."""

from __future__ import annotations

from fastapi import APIRouter, status

from api.dependencies import ActiveUser, DatabaseSession
from api.routes import plugin_auto_update as legacy
from modules.schemas.plugins import (
    ManagedPluginCreate,
    ManagedPluginUpdate,
    PluginAutoUpdateSettings,
)

from .schemas import (
    ActionResult,
    ManagedPluginRegisterRequest,
    ManagedPluginUpdateView,
    PluginUpdatesPluginPatch,
    PluginUpdatesSettingsRequest,
    PluginUpdateStatusView,
    PluginUpdatesView,
)

router = APIRouter(prefix="/api/v1/servers", tags=["v1-plugin-updates"])


def _plugin_view(item) -> ManagedPluginUpdateView:
    return ManagedPluginUpdateView(
        id=int(item.id),
        server_id=int(item.server_id),
        source_type=str(item.source_type),
        source_key=str(item.source_key),
        display_name=str(item.display_name),
        repo_url=item.repo_url,
        market_plugin_id=item.market_plugin_id,
        framework_key=item.framework_key,
        installed_version=str(item.installed_version),
        latest_version=item.latest_version,
        auto_update_enabled=bool(item.auto_update_enabled),
        last_status=item.last_status,
        last_error=item.last_error,
        last_check_at=item.last_check_at,
        last_update_at=item.last_update_at,
        exclude_dirs=[str(path) for path in (getattr(item, "exclude_dirs", None) or [])],
        exclude_files=[str(path) for path in (getattr(item, "exclude_files", None) or [])],
        backup_before_update=bool(getattr(item, "backup_before_update", False)),
        restart_after_update=bool(getattr(item, "restart_after_update", False)),
    )


def _view(payload) -> PluginUpdatesView:
    return PluginUpdatesView(
        enable_plugin_auto_update=bool(payload.enable_plugin_auto_update),
        plugin_update_check_interval_hours=float(payload.plugin_update_check_interval_hours),
        last_plugin_update_check=payload.last_plugin_update_check,
        enable_plugin_post_update_commands=bool(payload.enable_plugin_post_update_commands),
        plugin_post_update_command_ids=list(payload.plugin_post_update_command_ids or []),
        plugins=[_plugin_view(item) for item in payload.plugins],
    )


@router.get("/{server_id}/plugin-updates", response_model=PluginUpdatesView)
async def get_plugin_updates(
    server_id: int, db: DatabaseSession, current_user: ActiveUser
) -> PluginUpdatesView:
    return _view(await legacy.get_configuration(server_id, db, current_user))


@router.put("/{server_id}/plugin-updates", response_model=PluginUpdatesView)
async def update_plugin_updates(
    server_id: int,
    body: PluginUpdatesSettingsRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> PluginUpdatesView:
    return _view(
        await legacy.update_settings(
            server_id,
            PluginAutoUpdateSettings(
                enable_plugin_auto_update=body.enable_plugin_auto_update,
                plugin_update_check_interval_hours=body.plugin_update_check_interval_hours,
                enable_plugin_post_update_commands=body.enable_plugin_post_update_commands,
                plugin_post_update_command_ids=list(body.plugin_post_update_command_ids),
            ),
            db,
            current_user,
        )
    )


@router.post(
    "/{server_id}/plugin-updates/plugins",
    response_model=ManagedPluginUpdateView,
    status_code=status.HTTP_201_CREATED,
)
async def register_managed_plugin(
    server_id: int,
    body: ManagedPluginRegisterRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ManagedPluginUpdateView:
    return _plugin_view(
        await legacy.register_plugin(
            server_id,
            ManagedPluginCreate(**body.model_dump()),
            db,
            current_user,
        )
    )


@router.delete(
    "/{server_id}/plugin-updates/plugins/{plugin_id}",
    response_model=ActionResult,
)
async def unregister_managed_plugin(
    server_id: int,
    plugin_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ActionResult:
    result = await legacy.unmanage_plugin(server_id, plugin_id, db, current_user)
    return ActionResult(success=bool(result.success), message=str(result.message))


@router.patch(
    "/{server_id}/plugin-updates/plugins/{plugin_id}",
    response_model=ManagedPluginUpdateView,
)
async def patch_managed_plugin(
    server_id: int,
    plugin_id: int,
    body: PluginUpdatesPluginPatch,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ManagedPluginUpdateView:
    return _plugin_view(
        await legacy.update_plugin(
            server_id,
            plugin_id,
            ManagedPluginUpdate(**body.model_dump(exclude_unset=True)),
            db,
            current_user,
        )
    )


@router.post("/{server_id}/plugin-updates/run", response_model=ActionResult, status_code=202)
async def run_plugin_updates(
    server_id: int, db: DatabaseSession, current_user: ActiveUser
) -> ActionResult:
    result = await legacy.run_now(server_id, db, current_user)
    return ActionResult(success=bool(result.success), message=str(result.message))


@router.post(
    "/{server_id}/plugin-updates/plugins/{plugin_id}/test",
    response_model=ActionResult,
    status_code=202,
)
async def test_plugin_update(
    server_id: int,
    plugin_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ActionResult:
    result = await legacy.test_plugin_update(server_id, plugin_id, db, current_user)
    return ActionResult(success=bool(result.success), message=str(result.message))


@router.get("/{server_id}/plugin-updates/status", response_model=PluginUpdateStatusView)
async def get_plugin_update_status(
    server_id: int, db: DatabaseSession, current_user: ActiveUser
) -> PluginUpdateStatusView:
    payload = await legacy.get_run_status(server_id, db, current_user)
    logs = payload.get("logs") or []
    return PluginUpdateStatusView(
        state=str(payload.get("state") or "idle"),
        phase=str(payload.get("phase") or "idle"),
        message=payload.get("message"),
        current=int(payload.get("current") or 0),
        total=int(payload.get("total") or 0),
        logs=[str(item) for item in logs],
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
    )
