"""MapChooser HTTP routes. Patchable names are resolved on the facade."""

from __future__ import annotations

import posixpath
import shlex
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, or_
from sqlmodel import select

from api.dependencies import ActiveUser, DatabaseSession
from api.routes.map_management import (
    CustomMapSyncRunRequest,
    CustomMapSyncUpdateRequest,
    MapAddRequest,
    MapChooserUninstallRequest,
    MapConfigUpdateRequest,
    MapEnabledUpdateRequest,
    MapIdentityRequest,
    MapPresetApplyRequest,
    PluginConfigUpdateRequest,
)
from modules import ManagedPlugin, ScheduledTask
from services.compat import LateBoundModule

host = LateBoundModule("api.routes.map_management")
router = APIRouter(prefix="/servers/{server_id}/maps", tags=["map-management"])


@router.get("/status")
async def get_map_management_status(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, object]:
    server = await host.get_server_with_permission(server_id, current_user, db)
    ssh_manager = await host._connect(server)
    try:
        return await host._inspect_prerequisites(ssh_manager, server)
    finally:
        await ssh_manager.disconnect()


@router.get("/custom-sync")
async def get_custom_map_sync(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, object]:
    server = await host.get_server_with_permission(server_id, current_user, db)
    tasks = await host._get_map_sync_tasks(db, server_id)
    return host._map_sync_payload(server, tasks[0] if tasks else None)


@router.put("/custom-sync")
async def update_custom_map_sync(
    server_id: int,
    request: CustomMapSyncUpdateRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, object]:
    server = await host.get_server_with_permission(server_id, current_user, db)
    try:
        normalized_url = await host.validate_remote_map_url(request.url)
    except host.RemoteMapPoolError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    tasks = await host._get_map_sync_tasks(db, server_id)
    task = (
        tasks[0]
        if tasks
        else ScheduledTask(
            server_id=server_id,
            name=host.MAP_POOL_SYNC_TASK_NAME,
            action=host.MAP_POOL_SYNC_ACTION,
            enabled=request.enabled,
            schedule_type="interval",
            schedule_value=str(request.interval_seconds),
        )
    )
    task.name = host.MAP_POOL_SYNC_TASK_NAME
    task.action = host.MAP_POOL_SYNC_ACTION
    task.enabled = request.enabled
    task.schedule_type = "interval"
    task.schedule_value = str(request.interval_seconds)
    try:
        task.next_run = (
            host.get_current_time() + timedelta(seconds=request.interval_seconds)
            if request.enabled
            else None
        )
    except OverflowError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Map-pool sync interval is too large",
        ) from exc
    for duplicate in tasks[1:]:
        duplicate.enabled = False
        duplicate.next_run = None
        db.add(duplicate)

    server.map_pool_sync_url = normalized_url
    db.add(server)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return {
        **host._map_sync_payload(server, task),
        "message": "Custom map-pool synchronization settings saved",
    }


@router.post("/custom-sync/run")
async def run_custom_map_sync(
    server_id: int,
    request: CustomMapSyncRunRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, object]:
    server = await host.get_server_with_permission(server_id, current_user, db)
    tasks = await host._get_map_sync_tasks(db, server_id)
    task = tasks[0] if tasks else None
    if not server.map_pool_sync_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Save a custom remote map-pool URL before synchronizing",
        )

    async with host.maintenance_lock_service.get(
        server_id,
        operation=host.MAP_POOL_SYNC_ACTION,
        wait=False,
    ):
        ssh_manager = await host._connect(server)
        try:
            prerequisites = await host._inspect_prerequisites(ssh_manager, server)
            host._require_prerequisites(prerequisites)
            current_content, _ = await host._read_maps_config(
                ssh_manager,
                server,
                bool(prerequisites["maps_file_exists"]),
            )
            if request.expected_revision != host.content_revision(current_content):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="maps.txt changed on the server. Reload it before synchronizing.",
                )

            try:
                updated_content = await host.fetch_remote_map_pool(server.map_pool_sync_url)
                await host._replace_maps_config(ssh_manager, server, updated_content)
            except host.RemoteMapPoolError as exc:
                await host._record_map_sync_result(db, task, success=False, error=str(exc))
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=str(exc),
                ) from exc

            await host._record_map_sync_result(db, task, success=True)
            prerequisites["maps_file_exists"] = True
            payload = host._config_payload(
                updated_content,
                maps_file_exists=True,
                prerequisites=prerequisites,
            )
            return {
                **payload,
                "map_count": host._map_count(payload),
                "custom_sync": host._map_sync_payload(server, task),
                "message": "Custom remote map pool synchronized successfully",
            }
        finally:
            await ssh_manager.disconnect()


@router.delete("/plugin")
async def uninstall_mapchooser_plugin(
    server_id: int,
    request: MapChooserUninstallRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, object]:
    if request.confirmation != host.MAPCHOOSER_UNINSTALL_CONFIRMATION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="MapChooser uninstall confirmation did not match",
        )

    server = await host.get_server_with_permission(server_id, current_user, db)
    paths = host._remote_paths(server)
    plugin_directory = posixpath.normpath(paths["mapchooser_plugin_dir"])
    plugins_directory = posixpath.normpath(paths["plugins"])
    if (
        posixpath.dirname(plugin_directory) != plugins_directory
        or posixpath.basename(plugin_directory) != "MapChooser"
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Refusing to remove an unexpected plugin path",
        )

    async with host.maintenance_lock_service.get(
        server_id,
        operation="mapchooser_uninstall",
        wait=False,
    ):
        ssh_manager = await host._connect(server)
        try:
            quoted_plugin_directory = shlex.quote(plugin_directory)
            command = (
                f"if test -e {quoted_plugin_directory}; then "
                f"rm -rf -- {quoted_plugin_directory}; fi; "
                f"test ! -e {quoted_plugin_directory}"
            )
            success, stdout, stderr = await ssh_manager.execute_command(command, timeout=30)
            if not success:
                detail = (stderr or stdout or "plugin directory removal failed").strip()
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Unable to uninstall MapChooser: {detail}",
                )
        finally:
            await ssh_manager.disconnect()

        tasks = await host._get_map_sync_tasks(db, server_id)
        for task in tasks:
            task.enabled = False
            task.next_run = None
            db.add(task)

        tracked_result = await db.execute(
            select(ManagedPlugin).where(
                ManagedPlugin.server_id == server_id,
                or_(
                    func.lower(ManagedPlugin.display_name) == host.PLUGIN_CENTER_NAME.lower(),
                    func.lower(ManagedPlugin.repo_url).like("%/cs2-upkk-panelplg-mapchooser"),
                ),
            )
        )
        for tracked_plugin in tracked_result.scalars().all():
            tracked_plugin.auto_update_enabled = False
            tracked_plugin.last_status = "uninstalled"
            tracked_plugin.last_error = None
            db.add(tracked_plugin)
        await db.commit()

    return {
        "success": True,
        "deleted_path": plugin_directory,
        "mapchooser_installed": False,
        "ready": False,
        "message": "MapChooser plugin directory removed",
    }


@router.get("/plugin-config")
async def get_plugin_config(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, object]:
    server = await host.get_server_with_permission(server_id, current_user, db)
    ssh_manager = await host._connect(server)
    try:
        prerequisites = await host._inspect_prerequisites(ssh_manager, server)
        host._require_prerequisites(prerequisites)
        content, file_exists = await host._read_plugin_config(
            ssh_manager,
            server,
            bool(prerequisites["plugin_config_file_exists"]),
        )
        return host._plugin_config_payload(
            content,
            config_file_exists=file_exists,
            prerequisites=prerequisites,
        )
    finally:
        await ssh_manager.disconnect()


@router.put("/plugin-config")
async def update_mapchooser_plugin_config(
    server_id: int,
    request: PluginConfigUpdateRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, object]:
    server = await host.get_server_with_permission(server_id, current_user, db)
    async with host.maintenance_lock_service.get(server_id, operation="map_config", wait=False):
        ssh_manager = await host._connect(server)
        try:
            prerequisites = await host._inspect_prerequisites(ssh_manager, server)
            host._require_prerequisites(prerequisites)
            current_content, _ = await host._read_plugin_config(
                ssh_manager,
                server,
                bool(prerequisites["plugin_config_file_exists"]),
            )
            if request.expected_revision and request.expected_revision != host.content_revision(
                current_content
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="MapChooser config.json changed on the server. Reload it before saving.",
                )
            try:
                updated_content = host.update_plugin_config(current_content, request.values)
            except host.PluginConfigError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Invalid MapChooser config.json update: {exc}",
                ) from exc

            await host._replace_plugin_config(ssh_manager, server, updated_content)
            prerequisites["plugin_config_file_exists"] = True
            return {
                **host._plugin_config_payload(
                    updated_content,
                    config_file_exists=True,
                    prerequisites=prerequisites,
                ),
                "message": "MapChooser config.json saved successfully",
            }
        finally:
            await ssh_manager.disconnect()


@router.get("")
async def get_maps_config(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, object]:
    server = await host.get_server_with_permission(server_id, current_user, db)
    ssh_manager = await host._connect(server)
    try:
        prerequisites = await host._inspect_prerequisites(ssh_manager, server)
        host._require_prerequisites(prerequisites)
        content, file_exists = await host._read_maps_config(
            ssh_manager,
            server,
            bool(prerequisites["maps_file_exists"]),
        )
        return host._config_payload(
            content,
            maps_file_exists=file_exists,
            prerequisites=prerequisites,
        )
    finally:
        await ssh_manager.disconnect()


@router.put("")
async def update_maps_config(
    server_id: int,
    request: MapConfigUpdateRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, object]:
    server = await host.get_server_with_permission(server_id, current_user, db)
    async with host.maintenance_lock_service.get(server_id, operation="map_add", wait=False):
        ssh_manager = await host._connect(server)
        try:
            prerequisites = await host._inspect_prerequisites(ssh_manager, server)
            host._require_prerequisites(prerequisites)
            try:
                host.parse_maps_config(request.content)
            except host.MapConfigError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Invalid maps.txt: {exc}",
                ) from exc
            current_content, _ = await host._read_maps_config(
                ssh_manager,
                server,
                bool(prerequisites["maps_file_exists"]),
            )
            if request.expected_revision and request.expected_revision != host.content_revision(
                current_content
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="maps.txt changed on the server. Reload it before saving.",
                )
            await host._replace_maps_config(ssh_manager, server, request.content)
            prerequisites["maps_file_exists"] = True
            return {
                **host._config_payload(
                    request.content,
                    maps_file_exists=True,
                    prerequisites=prerequisites,
                ),
                "message": "maps.txt saved successfully",
            }
        finally:
            await ssh_manager.disconnect()


@router.post("/preset")
async def apply_map_preset(
    server_id: int,
    request: MapPresetApplyRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, object]:
    server = await host.get_server_with_permission(server_id, current_user, db)
    async with host.maintenance_lock_service.get(server_id, operation="map_preset", wait=False):
        ssh_manager = await host._connect(server)
        try:
            prerequisites = await host._inspect_prerequisites(ssh_manager, server)
            host._require_prerequisites(prerequisites)
            current_maps_content, _ = await host._read_maps_config(
                ssh_manager,
                server,
                bool(prerequisites["maps_file_exists"]),
            )
            if request.expected_revision != host.content_revision(current_maps_content):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="maps.txt changed on the server. Reload it before switching presets.",
                )

            if request.preset == "official":
                updated_maps_content = await host._official_maps_config(ssh_manager, server)
            else:
                updated_maps_content = await host._remote_maps_config(request.preset)

            plugin_config_payload: Optional[dict[str, object]] = None
            if request.preset == "kz":
                current_plugin_content, plugin_file_exists = await host._read_plugin_config(
                    ssh_manager,
                    server,
                    bool(prerequisites["plugin_config_file_exists"]),
                )
                if (
                    request.plugin_config_expected_revision
                    and request.plugin_config_expected_revision
                    != host.content_revision(current_plugin_content)
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "MapChooser config.json changed on the server. "
                            "Reload it before switching to the KZ preset."
                        ),
                    )
                try:
                    updated_plugin_content = host.update_plugin_config(
                        current_plugin_content,
                        host.KZ_PLUGIN_CONFIG,
                        allow_missing_known_fields=True,
                    )
                except host.PluginConfigError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail=f"Unable to apply the KZ MapChooser settings: {exc}",
                    ) from exc

                await host._replace_plugin_config(ssh_manager, server, updated_plugin_content)
                prerequisites["plugin_config_file_exists"] = True
                plugin_config_payload = host._plugin_config_payload(
                    updated_plugin_content,
                    config_file_exists=True,
                    prerequisites=prerequisites,
                )
                if not plugin_file_exists:
                    host.logger.info("Created MapChooser config.json while applying KZ preset")

            await host._replace_maps_config(ssh_manager, server, updated_maps_content)
            prerequisites["maps_file_exists"] = True
            maps_payload = host._config_payload(
                updated_maps_content,
                maps_file_exists=True,
                prerequisites=prerequisites,
            )
            return {
                **maps_payload,
                "preset": request.preset,
                "map_count": host._map_count(maps_payload),
                "plugin_config": plugin_config_payload,
                "message": f"Applied the {request.preset.upper()} map preset",
            }
        finally:
            await ssh_manager.disconnect()


@router.post("")
async def add_map(
    server_id: int,
    request: MapAddRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, object]:
    server = await host.get_server_with_permission(server_id, current_user, db)
    async with host.maintenance_lock_service.get(server_id, operation="map_update", wait=False):
        ssh_manager = await host._connect(server)
        try:
            prerequisites = await host._inspect_prerequisites(ssh_manager, server)
            host._require_prerequisites(prerequisites)
            try:
                workshop_id = host.normalize_workshop_id(request.workshop_id)
                restricted_times = host.validate_restricted_times(request.restricted_times)
            except host.MapConfigError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=str(exc),
                ) from exc
            content, _ = await host._read_maps_config(
                ssh_manager,
                server,
                bool(prerequisites["maps_file_exists"]),
            )

            name = request.name.strip() if request.name else ""
            if not name:
                name = await host._fetch_workshop_title(workshop_id) or ""
                if not name:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail="Unable to retrieve the Workshop title. Enter the map name manually and try again.",
                    )
            try:
                name = host.sanitize_map_name(name)
                updated_content = host.append_map_to_config(
                    content,
                    name=name,
                    workshop_id=workshop_id,
                    enabled=request.enabled,
                    min_players=request.min_players,
                    only_nominate=request.only_nominate,
                    restricted_times=restricted_times,
                )
            except host.MapConfigError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT
                    if "already exists" in str(exc)
                    else status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=str(exc),
                ) from exc

            await host._replace_maps_config(ssh_manager, server, updated_content)
            prerequisites["maps_file_exists"] = True
            return {
                **host._config_payload(
                    updated_content,
                    maps_file_exists=True,
                    prerequisites=prerequisites,
                ),
                "added_map": {"name": name, "workshop_id": workshop_id},
                "message": f"Added {name} to maps.txt",
            }
        finally:
            await ssh_manager.disconnect()


@router.patch("")
async def update_map_enabled(
    server_id: int,
    request: MapEnabledUpdateRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, object]:
    server = await host.get_server_with_permission(server_id, current_user, db)
    async with host.maintenance_lock_service.get(server_id, operation="map_delete", wait=False):
        ssh_manager = await host._connect(server)
        try:
            prerequisites = await host._inspect_prerequisites(ssh_manager, server)
            host._require_prerequisites(prerequisites)
            content, _ = await host._read_maps_config(
                ssh_manager,
                server,
                bool(prerequisites["maps_file_exists"]),
            )
            if request.expected_revision != host.content_revision(content):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="maps.txt changed on the server. Reload it before changing this map.",
                )
            try:
                updated_content = host.set_map_enabled(
                    content,
                    name=request.name,
                    workshop_id=request.workshop_id,
                    enabled=request.enabled,
                )
            except host.MapConfigError as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND
                    if "was not found" in str(exc)
                    else status.HTTP_409_CONFLICT,
                    detail=str(exc),
                ) from exc

            await host._replace_maps_config(ssh_manager, server, updated_content)
            prerequisites["maps_file_exists"] = True
            return {
                **host._config_payload(
                    updated_content,
                    maps_file_exists=True,
                    prerequisites=prerequisites,
                ),
                "message": f"{'Enabled' if request.enabled else 'Disabled'} {request.name}",
            }
        finally:
            await ssh_manager.disconnect()


@router.delete("")
async def delete_map(
    server_id: int,
    request: MapIdentityRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, object]:
    server = await host.get_server_with_permission(server_id, current_user, db)
    async with host.maintenance_lock_service.get(server_id, operation="map_batch", wait=False):
        ssh_manager = await host._connect(server)
        try:
            prerequisites = await host._inspect_prerequisites(ssh_manager, server)
            host._require_prerequisites(prerequisites)
            content, _ = await host._read_maps_config(
                ssh_manager,
                server,
                bool(prerequisites["maps_file_exists"]),
            )
            if request.expected_revision != host.content_revision(content):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="maps.txt changed on the server. Reload it before deleting this map.",
                )
            try:
                updated_content = host.remove_map_from_config(
                    content,
                    name=request.name,
                    workshop_id=request.workshop_id,
                )
            except host.MapConfigError as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND
                    if "was not found" in str(exc)
                    else status.HTTP_409_CONFLICT,
                    detail=str(exc),
                ) from exc

            await host._replace_maps_config(ssh_manager, server, updated_content)
            prerequisites["maps_file_exists"] = True
            return {
                **host._config_payload(
                    updated_content,
                    maps_file_exists=True,
                    prerequisites=prerequisites,
                ),
                "message": f"Removed {request.name} from maps.txt",
            }
        finally:
            await ssh_manager.disconnect()
