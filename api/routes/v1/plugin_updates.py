"""Versioned plugin auto-update workspace for one game server."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from api.dependencies import ActiveUser, DatabaseSession, require_server_access
from api.routes import plugin_auto_update as legacy
from modules import ManagedPlugin
from modules.schemas.plugins import (
    ManagedPluginCreate,
    ManagedPluginUpdate,
    PluginAutoUpdateSettings,
)
from services.audit_log_service import record_audit_event
from services.server_operation_hub import ServerOperationConflict

from .operation_locks import reject_stuck_lock_unless_active
from .operation_runner import enqueue_plugin_auto_update
from .operations import to_view
from .schemas import (
    ActionResult,
    ManagedPluginRegisterRequest,
    ManagedPluginUpdateView,
    PluginUpdatesPluginPatch,
    PluginUpdatesSettingsRequest,
    PluginUpdateStatusView,
    PluginUpdatesView,
    ServerOperationView,
)

router = APIRouter(prefix="/api/v1/servers", tags=["v1-plugin-updates"])


def _status_log_line(item: object) -> str:
    if isinstance(item, dict):
        message = str(item.get("message") or "").strip()
        raw_time = item.get("time")
        if raw_time and message:
            return f"{raw_time} {message}"
        return message or str(item)
    return str(item)


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
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> PluginUpdatesView:
    view = _view(
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
    await record_audit_event(
        category="config",
        action="config.plugin_updates",
        status="success",
        user=current_user,
        request=request,
        server_id=server_id,
        details={
            "enable_plugin_auto_update": body.enable_plugin_auto_update,
            "plugin_update_check_interval_hours": body.plugin_update_check_interval_hours,
        },
    )
    return view


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


@router.post(
    "/{server_id}/plugin-updates/run",
    response_model=ServerOperationView,
    status_code=202,
)
async def run_plugin_updates(
    server_id: int,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    await require_server_access(db, server_id, current_user)
    await reject_stuck_lock_unless_active(server_id)
    try:
        record = await enqueue_plugin_auto_update(
            server_id=server_id,
            actor_user_id=current_user.id,
            force=True,
        )
    except ServerOperationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await record_audit_event(
        category="plugin",
        action="plugin.auto_update.run",
        status="requested",
        user=current_user,
        request=request,
        server_id=server_id,
        details={"operation_id": record["operation_id"]},
    )
    return to_view(record)


@router.post(
    "/{server_id}/plugin-updates/plugins/{plugin_id}/test",
    response_model=ServerOperationView,
    status_code=202,
)
async def test_plugin_update(
    server_id: int,
    plugin_id: int,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    await require_server_access(db, server_id, current_user)
    plugin = await db.get(ManagedPlugin, plugin_id)
    if plugin is None or plugin.server_id != server_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Managed plugin not found"
        )
    await reject_stuck_lock_unless_active(server_id)
    try:
        record = await enqueue_plugin_auto_update(
            server_id=server_id,
            actor_user_id=current_user.id,
            plugin_id=plugin_id,
            force=True,
        )
    except ServerOperationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await record_audit_event(
        category="plugin",
        action="plugin.auto_update.test",
        status="requested",
        user=current_user,
        request=request,
        server_id=server_id,
        details={"operation_id": record["operation_id"], "plugin_id": plugin_id},
    )
    return to_view(record)


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
        logs=[_status_log_line(item) for item in logs],
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
    )
