"""Versioned MapChooser workspace for the Next.js console."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status

from api.dependencies import ActiveUser, DatabaseSession, require_server_access
from api.routes import map_management as legacy
from api.routes.map_management import (
    CustomMapSyncRunRequest,
    CustomMapSyncUpdateRequest,
    PluginConfigUpdateRequest,
)
from api.routes.map_management import (
    MapAddRequest as LegacyMapAddRequest,
)
from api.routes.map_management import (
    MapChooserUninstallRequest as LegacyMapChooserUninstallRequest,
)
from api.routes.map_management import (
    MapPresetApplyRequest as LegacyMapPresetApplyRequest,
)
from modules import Server
from services.audit_log_service import record_audit_event
from services.maintenance_lock import maintenance_lock_service
from services.map_management_service import (
    MapConfigError,
    content_revision,
    remove_map_from_config,
    set_map_enabled,
)

from .schemas import (
    MapAddRequest,
    MapChooserUninstallRequest,
    MapEnabledPatchRequest,
    MapEntryView,
    MapPluginConfigUpdateRequest,
    MapPluginConfigView,
    MapPluginFieldView,
    MapPoolIdentityRequest,
    MapPresetApplyRequest,
    MapsWorkspaceView,
    MapSyncRunRequest,
    MapSyncUpdateRequest,
    MapSyncView,
)

router = APIRouter(prefix="/api/v1/servers/{server_id}/maps", tags=["v1-maps"])


async def _audit_maps(
    action: str,
    user,
    request: Request,
    server_id: int,
    details: dict | None = None,
) -> None:
    await record_audit_event(
        category="config",
        action=action,
        status="success",
        user=user,
        request=request,
        server_id=server_id,
        details=details or {},
    )


def _entry(raw: dict[str, object]) -> MapEntryView:
    return MapEntryView(
        name=str(raw.get("name") or ""),
        workshop_id=str(raw.get("workshop_id") or ""),
        enabled=bool(raw.get("enabled", True)),
        filename=str(raw.get("filename") or raw.get("name") or ""),
        min_players=str(raw.get("min_players") or ""),
        only_nominate=bool(raw.get("only_nominate", False)),
        restricted_times=str(raw.get("restricted_times") or ""),
    )


def _plugin_value(value: object) -> bool | int | float | str:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        return value
    return "" if value is None else str(value)


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except TypeError, ValueError:
            return default
    return default


def _plugin_config(raw: dict[str, object] | None) -> MapPluginConfigView | None:
    if not raw:
        return None
    fields: list[MapPluginFieldView] = []
    for item in _as_list(raw.get("fields")):
        if not isinstance(item, dict):
            continue
        fields.append(
            MapPluginFieldView(
                key=str(item.get("key") or ""),
                kind=str(item.get("kind") or "string"),
                value=_plugin_value(item.get("value")),
                group=str(item.get("group") or "other"),
                known=bool(item.get("known", True)),
            )
        )
    config_error = raw.get("config_error")
    return MapPluginConfigView(
        revision=str(raw.get("revision") or ""),
        file_exists=bool(raw.get("plugin_config_file_exists", raw.get("file_exists", False))),
        fields=fields,
        unsupported_fields=[str(item) for item in _as_list(raw.get("unsupported_fields"))],
        config_error=config_error if isinstance(config_error, str) else None,
    )


def _maybe_dt(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _sync_view(raw: dict[str, object]) -> MapSyncView:
    try:
        interval = _as_int(raw.get("interval_seconds"), 300)
    except TypeError, ValueError:
        interval = 300
    return MapSyncView(
        url=str(raw.get("url") or ""),
        enabled=bool(raw.get("enabled", False)),
        interval_seconds=max(300, interval),
        last_run=_maybe_dt(raw.get("last_run")),
        next_run=_maybe_dt(raw.get("next_run")),
        last_status=str(raw["last_status"]) if raw.get("last_status") else None,
        last_error=str(raw["last_error"]) if raw.get("last_error") else None,
        run_count=_as_int(raw.get("run_count"), 0),
    )


def _http_detail(detail: object, fallback: str) -> str:
    if isinstance(detail, str) and detail.strip():
        return detail
    if isinstance(detail, dict):
        message = detail.get("message")
        if isinstance(message, str) and message.strip():
            return message
    return fallback


def _workspace(
    server_id: int,
    *,
    sync: MapSyncView,
    ssh_ok: bool,
    ssh_error: str | None = None,
    payload: dict[str, object] | None = None,
    plugin_config: dict[str, object] | None = None,
    message: str | None = None,
) -> MapsWorkspaceView:
    data = payload or {}
    config_error = data.get("config_error")
    return MapsWorkspaceView(
        server_id=server_id,
        ssh_ok=ssh_ok,
        ssh_error=ssh_error,
        ready=bool(data.get("ready", False)),
        counterstrikesharp_installed=bool(data.get("counterstrikesharp_installed", False)),
        mapchooser_installed=bool(data.get("mapchooser_installed", False)),
        maps_file_exists=bool(data.get("maps_file_exists", False)),
        plugin_config_file_exists=bool(data.get("plugin_config_file_exists", False)),
        maps_path=str(data["maps_path"]) if data.get("maps_path") else None,
        plugin_config_path=str(data["plugin_config_path"])
        if data.get("plugin_config_path")
        else None,
        plugin_center_name=str(data["plugin_center_name"])
        if data.get("plugin_center_name")
        else None,
        maps=[_entry(item) for item in _as_list(data.get("maps")) if isinstance(item, dict)],
        revision=str(data["revision"]) if data.get("revision") else None,
        config_error=config_error if isinstance(config_error, str) else None,
        plugin_config=_plugin_config(plugin_config),
        custom_sync=sync,
        message=message or (str(data["message"]) if data.get("message") else None),
    )


async def _sync_for(db, server: Server) -> MapSyncView:
    tasks = await legacy._get_map_sync_tasks(db, server.id)
    return _sync_view(legacy._map_sync_payload(server, tasks[0] if tasks else None))


async def _try_connect(server: Server):
    try:
        return await legacy._connect(server), None
    except HTTPException as exc:
        if exc.status_code == status.HTTP_502_BAD_GATEWAY:
            return None, _http_detail(exc.detail, "SSH connection failed")
        raise


async def _load_workspace(server: Server, db) -> MapsWorkspaceView:
    sync = await _sync_for(db, server)
    ssh, ssh_error = await _try_connect(server)
    if ssh is None:
        return _workspace(server.id, sync=sync, ssh_ok=False, ssh_error=ssh_error)
    try:
        try:
            prerequisites = await legacy._inspect_prerequisites(ssh, server)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_502_BAD_GATEWAY:
                return _workspace(
                    server.id,
                    sync=sync,
                    ssh_ok=False,
                    ssh_error=_http_detail(exc.detail, "Unable to inspect map prerequisites"),
                )
            raise
        plugin_payload = None
        maps_payload: dict[str, object] = dict(prerequisites)
        if prerequisites.get("ready"):
            content, file_exists = await legacy._read_maps_config(
                ssh,
                server,
                bool(prerequisites.get("maps_file_exists")),
            )
            maps_payload = legacy._config_payload(
                content,
                maps_file_exists=file_exists,
                prerequisites=prerequisites,
            )
            plugin_content, plugin_exists = await legacy._read_plugin_config(
                ssh,
                server,
                bool(prerequisites.get("plugin_config_file_exists")),
            )
            plugin_payload = legacy._plugin_config_payload(
                plugin_content,
                config_file_exists=plugin_exists,
                prerequisites=prerequisites,
            )
        return _workspace(
            server.id,
            sync=sync,
            ssh_ok=True,
            payload=maps_payload,
            plugin_config=plugin_payload,
        )
    finally:
        await ssh.disconnect()


@router.get("", response_model=MapsWorkspaceView)
async def get_maps_workspace(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> MapsWorkspaceView:
    """Return the map pool and prerequisites. SSH failures stay 200."""
    server = await require_server_access(db, server_id, current_user)
    return await _load_workspace(server, db)


@router.post("", response_model=MapsWorkspaceView)
async def add_map(
    server_id: int,
    body: MapAddRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> MapsWorkspaceView:
    server = await require_server_access(db, server_id, current_user)
    payload = await legacy.add_map(
        server_id,
        LegacyMapAddRequest(**body.model_dump()),
        db,
        current_user,
    )
    await _audit_maps(
        "config.maps.add",
        current_user,
        request,
        server_id,
        {"name": body.name, "workshop_id": body.workshop_id},
    )
    return _workspace(
        server_id,
        sync=await _sync_for(db, server),
        ssh_ok=True,
        payload=payload,
        message=str(payload.get("message") or ""),
    )


@router.patch("", response_model=MapsWorkspaceView)
async def update_map_enabled(
    server_id: int,
    body: MapEnabledPatchRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> MapsWorkspaceView:
    """Enable or disable a map. Official maps use an empty workshop_id."""
    server = await require_server_access(db, server_id, current_user)
    payload = await _mutate_map(
        server,
        name=body.name,
        workshop_id=body.workshop_id,
        expected_revision=body.expected_revision,
        enabled=body.enabled,
    )
    await _audit_maps(
        "config.maps.enable",
        current_user,
        request,
        server_id,
        {"name": body.name, "workshop_id": body.workshop_id, "enabled": body.enabled},
    )
    return _workspace(
        server_id,
        sync=await _sync_for(db, server),
        ssh_ok=True,
        payload=payload,
        message=str(payload.get("message") or ""),
    )


@router.delete("", response_model=MapsWorkspaceView)
async def delete_map(
    server_id: int,
    body: MapPoolIdentityRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> MapsWorkspaceView:
    server = await require_server_access(db, server_id, current_user)
    payload = await _mutate_map(
        server,
        name=body.name,
        workshop_id=body.workshop_id,
        expected_revision=body.expected_revision,
        delete=True,
    )
    await _audit_maps(
        "config.maps.delete",
        current_user,
        request,
        server_id,
        {"name": body.name, "workshop_id": body.workshop_id},
    )
    return _workspace(
        server_id,
        sync=await _sync_for(db, server),
        ssh_ok=True,
        payload=payload,
        message=str(payload.get("message") or ""),
    )


async def _mutate_map(
    server: Server,
    *,
    name: str,
    workshop_id: str,
    expected_revision: str,
    enabled: bool | None = None,
    delete: bool = False,
) -> dict[str, object]:
    """Toggle or remove a pool entry, including official maps with no workshop id."""
    async with maintenance_lock_service.get(server.id, operation="map_update", wait=False):
        ssh = await legacy._connect(server)
        try:
            prerequisites = await legacy._inspect_prerequisites(ssh, server)
            legacy._require_prerequisites(prerequisites)
            content, _ = await legacy._read_maps_config(
                ssh,
                server,
                bool(prerequisites.get("maps_file_exists")),
            )
            if expected_revision != content_revision(content):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="maps.txt changed on the server. Reload it before changing this map.",
                )
            try:
                if delete:
                    updated = remove_map_from_config(content, name=name, workshop_id=workshop_id)
                    message = f"Removed {name}"
                else:
                    updated = set_map_enabled(
                        content,
                        name=name,
                        workshop_id=workshop_id,
                        enabled=bool(enabled),
                    )
                    message = f"{'Enabled' if enabled else 'Disabled'} {name}"
            except MapConfigError as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND
                    if "was not found" in str(exc)
                    else status.HTTP_409_CONFLICT,
                    detail=str(exc),
                ) from exc
            await legacy._replace_maps_config(ssh, server, updated)
            prerequisites["maps_file_exists"] = True
            return {
                **legacy._config_payload(
                    updated,
                    maps_file_exists=True,
                    prerequisites=prerequisites,
                ),
                "message": message,
            }
        finally:
            await ssh.disconnect()


@router.post("/presets", response_model=MapsWorkspaceView)
async def apply_map_preset(
    server_id: int,
    body: MapPresetApplyRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> MapsWorkspaceView:
    server = await require_server_access(db, server_id, current_user)
    payload = await legacy.apply_map_preset(
        server_id,
        LegacyMapPresetApplyRequest(**body.model_dump()),
        db,
        current_user,
    )
    await _audit_maps(
        "config.maps.preset",
        current_user,
        request,
        server_id,
        {"preset": body.preset},
    )
    plugin = payload.get("plugin_config")
    return _workspace(
        server_id,
        sync=await _sync_for(db, server),
        ssh_ok=True,
        payload=payload,
        plugin_config=plugin if isinstance(plugin, dict) else None,
        message=str(payload.get("message") or ""),
    )


@router.delete("/plugin", response_model=MapsWorkspaceView)
async def uninstall_mapchooser_plugin(
    server_id: int,
    body: MapChooserUninstallRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> MapsWorkspaceView:
    server = await require_server_access(db, server_id, current_user)
    await legacy.uninstall_mapchooser_plugin(
        server_id,
        LegacyMapChooserUninstallRequest(confirmation=body.confirmation),
        db,
        current_user,
    )
    await _audit_maps("config.maps.plugin_uninstall", current_user, request, server_id)
    workspace = await _load_workspace(server, db)
    return workspace.model_copy(update={"message": "MapChooser plugin directory removed"})


@router.put("/plugin-config", response_model=MapsWorkspaceView)
async def update_plugin_config(
    server_id: int,
    body: MapPluginConfigUpdateRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> MapsWorkspaceView:
    server = await require_server_access(db, server_id, current_user)
    await legacy.update_mapchooser_plugin_config(
        server_id,
        PluginConfigUpdateRequest(**body.model_dump()),
        db,
        current_user,
    )
    await _audit_maps("config.maps.plugin_config", current_user, request, server_id)
    workspace = await _load_workspace(server, db)
    return workspace.model_copy(update={"message": "MapChooser config.json saved successfully"})


@router.put("/custom-sync", response_model=MapsWorkspaceView)
async def update_custom_sync(
    server_id: int,
    body: MapSyncUpdateRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> MapsWorkspaceView:
    server = await require_server_access(db, server_id, current_user)
    await legacy.update_custom_map_sync(
        server_id,
        CustomMapSyncUpdateRequest(**body.model_dump()),
        db,
        current_user,
    )
    await _audit_maps("config.maps.custom_sync", current_user, request, server_id)
    workspace = await _load_workspace(server, db)
    return workspace.model_copy(
        update={"message": "Custom map-pool synchronization settings saved"}
    )


@router.post("/custom-sync/run", response_model=MapsWorkspaceView)
async def run_custom_sync(
    server_id: int,
    body: MapSyncRunRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> MapsWorkspaceView:
    await require_server_access(db, server_id, current_user)
    payload = await legacy.run_custom_map_sync(
        server_id,
        CustomMapSyncRunRequest(expected_revision=body.expected_revision),
        db,
        current_user,
    )
    await _audit_maps("config.maps.custom_sync_run", current_user, request, server_id)
    return _workspace(
        server_id,
        sync=_sync_view(_as_dict(payload.get("custom_sync"))),
        ssh_ok=True,
        payload=payload,
        message=str(payload.get("message") or ""),
    )
