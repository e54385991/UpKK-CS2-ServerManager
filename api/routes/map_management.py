"""MapChooser map-pool management routes."""

from __future__ import annotations

import logging
import posixpath
import shlex
import uuid
from datetime import timedelta
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.dependencies import ActiveUser, DatabaseSession
from api.routes.servers import get_server_with_permission
from modules import ManagedPlugin, ScheduledTask, Server
from modules.http_helper import http_helper
from modules.utils import get_current_time
from services.maintenance_lock import maintenance_lock_service
from services.map_management_service import (
    DEFAULT_MAPS_CONFIG,
    DEFAULT_PLUGIN_CONFIG_CONTENT,
    MAX_MAPS_CONFIG_BYTES,
    MAX_PLUGIN_CONFIG_BYTES,
    MapConfigError,
    PluginConfigError,
    append_map_to_config,
    build_plugin_config_fields,
    content_revision,
    normalize_workshop_id,
    parse_maps_config,
    parse_plugin_config,
    remove_map_from_config,
    render_official_maps_config,
    sanitize_map_name,
    set_map_enabled,
    update_plugin_config,
    validate_restricted_times,
)
from services.remote_map_pool_service import (
    RemoteMapPoolError,
    fetch_remote_map_pool,
    validate_remote_map_url,
)
from services.ssh_manager import SSHManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/servers/{server_id}/maps", tags=["map-management"])

PLUGIN_CENTER_NAME = "CS2-Upkk-PanelPLG-Mapchooser"
PLUGIN_CENTER_URL = "/plugin-market?search=CS2-Upkk-PanelPLG-Mapchooser"
MAP_PRESET_URLS = {
    "kz": (
        "https://raw.githubusercontent.com/UpKK-Xnet-YYDCS/GeneralMapcfg_Public/"
        "refs/heads/master/cs2/kz/counterstrikesharp/configs/plugins/MapChooser/maps.txt"
    ),
    "ze": (
        "https://raw.githubusercontent.com/UpKK-Xnet-YYDCS/UPKK_ZE_PUBLIC/"
        "refs/heads/master/cs2/counterstrikesharp/configs/plugins/MapChooser/maps.txt"
    ),
}
KZ_PLUGIN_CONFIG = {
    "UseGameTimeLimit": False,
    "EnforceTimeLimit": True,
    "ChangeMapUse_host_workshop_map": True,
}
MAP_POOL_SYNC_ACTION = "map_pool_sync"
MAP_POOL_SYNC_TASK_NAME = "MapChooser custom map-pool sync"
MAP_POOL_SYNC_MIN_INTERVAL_SECONDS = 300
MAPCHOOSER_UNINSTALL_CONFIRMATION = "UNINSTALL MAPCHOOSER"
# Backward-compatible test/introspection alias; writes use the distributed service below.
_map_write_locks = maintenance_lock_service._locks


class MapConfigUpdateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MAPS_CONFIG_BYTES)
    expected_revision: Optional[str] = Field(default=None, min_length=64, max_length=64)


class PluginConfigUpdateRequest(BaseModel):
    values: dict[str, Any]
    expected_revision: Optional[str] = Field(default=None, min_length=64, max_length=64)


class MapAddRequest(BaseModel):
    workshop_id: str = Field(min_length=1, max_length=512)
    name: Optional[str] = Field(default=None, max_length=128)
    enabled: bool = True
    min_players: int = Field(default=0, ge=0, le=64)
    only_nominate: bool = False
    restricted_times: str = Field(default="", max_length=512)


class MapIdentityRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    workshop_id: str = Field(min_length=1, max_length=20, pattern=r"^[0-9]+$")
    expected_revision: str = Field(min_length=64, max_length=64)


class MapEnabledUpdateRequest(MapIdentityRequest):
    enabled: bool


class MapPresetApplyRequest(BaseModel):
    preset: Literal["official", "kz", "ze"]
    expected_revision: str = Field(min_length=64, max_length=64)
    plugin_config_expected_revision: Optional[str] = Field(
        default=None,
        min_length=64,
        max_length=64,
    )


class CustomMapSyncUpdateRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    interval_seconds: int = Field(
        default=3600,
        ge=MAP_POOL_SYNC_MIN_INTERVAL_SECONDS,
    )
    enabled: bool = False


class CustomMapSyncRunRequest(BaseModel):
    expected_revision: str = Field(min_length=64, max_length=64)


class MapChooserUninstallRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=64)


def _remote_paths(server: Server) -> dict[str, str]:
    csgo_dir = posixpath.join(server.game_directory.rstrip("/"), "cs2/game/csgo")
    css_dir = posixpath.join(csgo_dir, "addons/counterstrikesharp")
    return {
        "counterstrikesharp": css_dir,
        "plugins": posixpath.join(css_dir, "plugins"),
        "mapchooser_plugin_dir": posixpath.join(css_dir, "plugins/MapChooser"),
        "mapchooser_dll": posixpath.join(css_dir, "plugins/MapChooser/MapChooser.dll"),
        "game_maps": posixpath.join(csgo_dir, "maps"),
        "maps": posixpath.join(css_dir, "configs/plugins/MapChooser/maps.txt"),
        "config": posixpath.join(css_dir, "configs/plugins/MapChooser/config.json"),
    }


async def _connect(server: Server) -> SSHManager:
    ssh_manager = SSHManager()
    success, message = await ssh_manager.connect(server)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SSH connection failed: {message}",
        )
    return ssh_manager


async def _inspect_prerequisites(ssh_manager: SSHManager, server: Server) -> dict[str, object]:
    paths = _remote_paths(server)
    css_path = shlex.quote(paths["counterstrikesharp"])
    css_bin_path = shlex.quote(posixpath.join(paths["counterstrikesharp"], "bin"))
    plugins_path = shlex.quote(paths["plugins"])
    canonical_dll = shlex.quote(paths["mapchooser_dll"])
    maps_path = shlex.quote(paths["maps"])
    config_path = shlex.quote(paths["config"])
    command = (
        f"if test -d {css_path} && "
        f"find {css_bin_path} -maxdepth 5 -type f "
        "\\( -name CounterStrikeSharp.API.dll -o -name counterstrikesharp.so "
        "-o -name CounterStrikeSharp.dll \\) -print -quit 2>/dev/null | grep -q .; "
        "then printf 'counterstrikesharp=1\\n'; "
        "else printf 'counterstrikesharp=0\\n'; fi; "
        f"if test -f {canonical_dll} || "
        f"find {plugins_path} -maxdepth 4 -type f -name MapChooser.dll -print -quit 2>/dev/null | grep -q .; "
        "then printf 'mapchooser=1\\n'; else printf 'mapchooser=0\\n'; fi; "
        f"if test -f {maps_path}; then printf 'maps_file=1\\n'; "
        "else printf 'maps_file=0\\n'; fi; "
        f"if test -f {config_path}; then printf 'config_file=1\\n'; "
        "else printf 'config_file=0\\n'; fi"
    )
    success, stdout, stderr = await ssh_manager.execute_command(command, timeout=20)
    if not success:
        error = (stderr or stdout or "remote prerequisite check failed").strip()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to inspect map-management prerequisites: {error}",
        )

    markers: dict[str, str] = {}
    for line in stdout.splitlines():
        key, separator, value = line.strip().partition("=")
        if separator:
            markers[key] = value

    counterstrikesharp_installed = markers.get("counterstrikesharp") == "1"
    mapchooser_installed = markers.get("mapchooser") == "1"
    return {
        "counterstrikesharp_installed": counterstrikesharp_installed,
        "mapchooser_installed": mapchooser_installed,
        "maps_file_exists": markers.get("maps_file") == "1",
        "plugin_config_file_exists": markers.get("config_file") == "1",
        "ready": counterstrikesharp_installed and mapchooser_installed,
        "plugin_center_name": PLUGIN_CENTER_NAME,
        "plugin_center_url": PLUGIN_CENTER_URL,
        "counterstrikesharp_install_action": "install_counterstrikesharp",
        "maps_path": paths["maps"],
        "plugin_config_path": paths["config"],
        "mapchooser_plugin_path": paths["mapchooser_plugin_dir"],
    }


def _require_prerequisites(prerequisites: dict[str, object]) -> None:
    missing: list[str] = []
    if not prerequisites["counterstrikesharp_installed"]:
        missing.append("counterstrikesharp")
    if not prerequisites["mapchooser_installed"]:
        missing.append("mapchooser")
    if missing:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "map_management_prerequisites_missing",
                "message": "Install CounterStrikeSharp and CS2-Upkk-PanelPLG-Mapchooser before managing maps.",
                "missing": missing,
                "plugin_center_name": PLUGIN_CENTER_NAME,
                "plugin_center_url": PLUGIN_CENTER_URL,
            },
        )


async def _read_maps_config(
    ssh_manager: SSHManager,
    server: Server,
    maps_file_exists: bool,
) -> tuple[str, bool]:
    if not maps_file_exists:
        return DEFAULT_MAPS_CONFIG, False
    maps_path = _remote_paths(server)["maps"]
    success, content, error = await ssh_manager.read_file(
        maps_path,
        server,
        max_size=MAX_MAPS_CONFIG_BYTES,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to read maps.txt: {error}",
        )
    return content, True


async def _read_plugin_config(
    ssh_manager: SSHManager,
    server: Server,
    config_file_exists: bool,
) -> tuple[str, bool]:
    if not config_file_exists:
        return DEFAULT_PLUGIN_CONFIG_CONTENT, False
    config_path = _remote_paths(server)["config"]
    success, content, error = await ssh_manager.read_file(
        config_path,
        server,
        max_size=MAX_PLUGIN_CONFIG_BYTES,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to read MapChooser config.json: {error}",
        )
    return content, True


async def _replace_maps_config(
    ssh_manager: SSHManager,
    server: Server,
    content: str,
) -> None:
    await _replace_remote_config(
        ssh_manager,
        server,
        _remote_paths(server)["maps"],
        content,
        "maps.txt",
    )


async def _replace_plugin_config(
    ssh_manager: SSHManager,
    server: Server,
    content: str,
) -> None:
    await _replace_remote_config(
        ssh_manager,
        server,
        _remote_paths(server)["config"],
        content,
        "MapChooser config.json",
    )


async def _replace_remote_config(
    ssh_manager: SSHManager,
    server: Server,
    target_path: str,
    content: str,
    display_name: str,
) -> None:
    parent_directory = posixpath.dirname(target_path)
    success, stdout, stderr = await ssh_manager.execute_command(
        f"mkdir -p -- {shlex.quote(parent_directory)}",
        timeout=20,
    )
    if not success:
        error = (stderr or stdout or "unable to create configuration directory").strip()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to prepare {display_name}: {error}",
        )

    temporary_path = f"{target_path}.upkk-{uuid.uuid4().hex}.tmp"
    success, error = await ssh_manager.write_file(temporary_path, content, server)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to stage {display_name}: {error}",
        )

    move_command = f"mv -f -- {shlex.quote(temporary_path)} {shlex.quote(target_path)}"
    success, stdout, stderr = await ssh_manager.execute_command(move_command, timeout=20)
    if not success:
        await ssh_manager.execute_command(f"rm -f -- {shlex.quote(temporary_path)}", timeout=10)
        error = (stderr or stdout or "atomic replace failed").strip()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to replace {display_name}: {error}",
        )


def _config_payload(
    content: str,
    *,
    maps_file_exists: bool,
    prerequisites: dict[str, object],
) -> dict[str, object]:
    config_error: Optional[str] = None
    try:
        parsed = parse_maps_config(content)
    except MapConfigError as exc:
        parsed_maps: list[dict[str, object]] = []
        config_error = f"Invalid maps.txt: {exc}"
    else:
        parsed_maps = parsed.maps
    return {
        **prerequisites,
        "maps_file_exists": maps_file_exists,
        "content": content,
        "revision": content_revision(content),
        "maps": parsed_maps,
        "config_error": config_error,
    }


def _plugin_config_payload(
    content: str,
    *,
    config_file_exists: bool,
    prerequisites: dict[str, object],
) -> dict[str, object]:
    config_error: Optional[str] = None
    try:
        config = parse_plugin_config(content)
        fields, unsupported_fields = build_plugin_config_fields(config)
    except PluginConfigError as exc:
        fields = []
        unsupported_fields = []
        config_error = f"Invalid MapChooser config.json: {exc}"
    return {
        **prerequisites,
        "plugin_config_file_exists": config_file_exists,
        "revision": content_revision(content),
        "fields": fields,
        "unsupported_fields": unsupported_fields,
        "config_error": config_error,
    }


async def _fetch_workshop_title(workshop_id: str) -> Optional[str]:
    success, data, error = await http_helper.post(
        "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/",
        headers={"User-Agent": "UpKK-CS2-ServerManager"},
        data={"itemcount": "1", "publishedfileids[0]": workshop_id},
        timeout=10,
    )
    if not success or not isinstance(data, dict):
        logger.warning("Unable to resolve Workshop title for %s: %s", workshop_id, error)
        return None
    response_data = data.get("response")
    if not isinstance(response_data, dict):
        return None
    details = response_data.get("publishedfiledetails", [])
    if not details or not isinstance(details[0], dict):
        return None
    title = details[0].get("title")
    return str(title).strip() if title else None


async def _official_maps_config(ssh_manager: SSHManager, server: Server) -> str:
    maps_directory = shlex.quote(_remote_paths(server)["game_maps"])
    command = f"find {maps_directory} -maxdepth 1 -type f -name '*.vpk' -printf '%f\\n' 2>/dev/null"
    success, stdout, stderr = await ssh_manager.execute_command(command, timeout=30)
    if not success:
        error = (stderr or stdout or "map directory scan failed").strip()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to scan official map VPK files: {error}",
        )

    map_names = [
        filename[:-4]
        for filename in stdout.splitlines()
        if filename.lower().endswith(".vpk") and len(filename) > 4
    ]
    try:
        return render_official_maps_config(map_names)
    except MapConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


async def _remote_maps_config(preset: Literal["kz", "ze"]) -> str:
    try:
        return await fetch_remote_map_pool(MAP_PRESET_URLS[preset])
    except RemoteMapPoolError as exc:
        logger.warning("Unable to fetch %s map preset: %s", preset, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to download the {preset.upper()} map preset: {exc}",
        ) from exc


async def _get_map_sync_tasks(
    db: AsyncSession,
    server_id: int,
) -> list[ScheduledTask]:
    result = await db.execute(
        select(ScheduledTask)
        .where(
            ScheduledTask.server_id == server_id,
            ScheduledTask.action == MAP_POOL_SYNC_ACTION,
        )
        .order_by(ScheduledTask.id.asc())
    )
    return list(result.scalars().all())


def _map_sync_payload(server: Server, task: Optional[ScheduledTask]) -> dict[str, object]:
    try:
        interval_seconds = int(task.schedule_value) if task else MAP_POOL_SYNC_MIN_INTERVAL_SECONDS
    except TypeError, ValueError:
        interval_seconds = MAP_POOL_SYNC_MIN_INTERVAL_SECONDS
    return {
        "url": server.map_pool_sync_url or "",
        "enabled": bool(task and task.enabled),
        "interval_seconds": max(MAP_POOL_SYNC_MIN_INTERVAL_SECONDS, interval_seconds),
        "last_run": task.last_run if task else None,
        "next_run": task.next_run if task else None,
        "last_status": task.last_status if task else None,
        "last_error": task.last_error if task else None,
        "run_count": task.run_count if task else 0,
    }


async def _record_map_sync_result(
    db: AsyncSession,
    task: Optional[ScheduledTask],
    *,
    success: bool,
    error: Optional[str] = None,
) -> None:
    if task is None:
        return
    now = get_current_time()
    try:
        interval_seconds = max(MAP_POOL_SYNC_MIN_INTERVAL_SECONDS, int(task.schedule_value))
    except TypeError, ValueError:
        interval_seconds = MAP_POOL_SYNC_MIN_INTERVAL_SECONDS
    task.last_run = now
    task.last_status = "success" if success else "failed"
    task.last_error = error
    task.run_count += 1
    task.next_run = now + timedelta(seconds=interval_seconds) if task.enabled else None
    db.add(task)
    await db.commit()


@router.get("/status")
async def get_map_management_status(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, object]:
    server = await get_server_with_permission(server_id, current_user, db)
    ssh_manager = await _connect(server)
    try:
        return await _inspect_prerequisites(ssh_manager, server)
    finally:
        await ssh_manager.disconnect()


@router.get("/custom-sync")
async def get_custom_map_sync(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, object]:
    server = await get_server_with_permission(server_id, current_user, db)
    tasks = await _get_map_sync_tasks(db, server_id)
    return _map_sync_payload(server, tasks[0] if tasks else None)


@router.put("/custom-sync")
async def update_custom_map_sync(
    server_id: int,
    request: CustomMapSyncUpdateRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, object]:
    server = await get_server_with_permission(server_id, current_user, db)
    try:
        normalized_url = await validate_remote_map_url(request.url)
    except RemoteMapPoolError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    tasks = await _get_map_sync_tasks(db, server_id)
    task = (
        tasks[0]
        if tasks
        else ScheduledTask(
            server_id=server_id,
            name=MAP_POOL_SYNC_TASK_NAME,
            action=MAP_POOL_SYNC_ACTION,
            enabled=request.enabled,
            schedule_type="interval",
            schedule_value=str(request.interval_seconds),
        )
    )
    task.name = MAP_POOL_SYNC_TASK_NAME
    task.action = MAP_POOL_SYNC_ACTION
    task.enabled = request.enabled
    task.schedule_type = "interval"
    task.schedule_value = str(request.interval_seconds)
    try:
        task.next_run = (
            get_current_time() + timedelta(seconds=request.interval_seconds)
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
        **_map_sync_payload(server, task),
        "message": "Custom map-pool synchronization settings saved",
    }


@router.post("/custom-sync/run")
async def run_custom_map_sync(
    server_id: int,
    request: CustomMapSyncRunRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, object]:
    server = await get_server_with_permission(server_id, current_user, db)
    tasks = await _get_map_sync_tasks(db, server_id)
    task = tasks[0] if tasks else None
    if not server.map_pool_sync_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Save a custom remote map-pool URL before synchronizing",
        )

    async with maintenance_lock_service.get(
        server_id,
        operation=MAP_POOL_SYNC_ACTION,
        wait=False,
    ):
        ssh_manager = await _connect(server)
        try:
            prerequisites = await _inspect_prerequisites(ssh_manager, server)
            _require_prerequisites(prerequisites)
            current_content, _ = await _read_maps_config(
                ssh_manager,
                server,
                bool(prerequisites["maps_file_exists"]),
            )
            if request.expected_revision != content_revision(current_content):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="maps.txt changed on the server. Reload it before synchronizing.",
                )

            try:
                updated_content = await fetch_remote_map_pool(server.map_pool_sync_url)
                await _replace_maps_config(ssh_manager, server, updated_content)
            except RemoteMapPoolError as exc:
                await _record_map_sync_result(db, task, success=False, error=str(exc))
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=str(exc),
                ) from exc

            await _record_map_sync_result(db, task, success=True)
            prerequisites["maps_file_exists"] = True
            payload = _config_payload(
                updated_content,
                maps_file_exists=True,
                prerequisites=prerequisites,
            )
            return {
                **payload,
                "map_count": len(payload["maps"]),
                "custom_sync": _map_sync_payload(server, task),
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
    if request.confirmation != MAPCHOOSER_UNINSTALL_CONFIRMATION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="MapChooser uninstall confirmation did not match",
        )

    server = await get_server_with_permission(server_id, current_user, db)
    paths = _remote_paths(server)
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

    async with maintenance_lock_service.get(
        server_id,
        operation="mapchooser_uninstall",
        wait=False,
    ):
        ssh_manager = await _connect(server)
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

        tasks = await _get_map_sync_tasks(db, server_id)
        for task in tasks:
            task.enabled = False
            task.next_run = None
            db.add(task)

        tracked_result = await db.execute(
            select(ManagedPlugin).where(
                ManagedPlugin.server_id == server_id,
                or_(
                    func.lower(ManagedPlugin.display_name) == PLUGIN_CENTER_NAME.lower(),
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
    server = await get_server_with_permission(server_id, current_user, db)
    ssh_manager = await _connect(server)
    try:
        prerequisites = await _inspect_prerequisites(ssh_manager, server)
        _require_prerequisites(prerequisites)
        content, file_exists = await _read_plugin_config(
            ssh_manager,
            server,
            bool(prerequisites["plugin_config_file_exists"]),
        )
        return _plugin_config_payload(
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
    server = await get_server_with_permission(server_id, current_user, db)
    async with maintenance_lock_service.get(server_id, operation="map_config", wait=False):
        ssh_manager = await _connect(server)
        try:
            prerequisites = await _inspect_prerequisites(ssh_manager, server)
            _require_prerequisites(prerequisites)
            current_content, _ = await _read_plugin_config(
                ssh_manager,
                server,
                bool(prerequisites["plugin_config_file_exists"]),
            )
            if request.expected_revision and request.expected_revision != content_revision(
                current_content
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="MapChooser config.json changed on the server. Reload it before saving.",
                )
            try:
                updated_content = update_plugin_config(current_content, request.values)
            except PluginConfigError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Invalid MapChooser config.json update: {exc}",
                ) from exc

            await _replace_plugin_config(ssh_manager, server, updated_content)
            prerequisites["plugin_config_file_exists"] = True
            return {
                **_plugin_config_payload(
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
    server = await get_server_with_permission(server_id, current_user, db)
    ssh_manager = await _connect(server)
    try:
        prerequisites = await _inspect_prerequisites(ssh_manager, server)
        _require_prerequisites(prerequisites)
        content, file_exists = await _read_maps_config(
            ssh_manager,
            server,
            bool(prerequisites["maps_file_exists"]),
        )
        return _config_payload(
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
    server = await get_server_with_permission(server_id, current_user, db)
    async with maintenance_lock_service.get(server_id, operation="map_add", wait=False):
        ssh_manager = await _connect(server)
        try:
            prerequisites = await _inspect_prerequisites(ssh_manager, server)
            _require_prerequisites(prerequisites)
            try:
                parse_maps_config(request.content)
            except MapConfigError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Invalid maps.txt: {exc}",
                ) from exc
            current_content, _ = await _read_maps_config(
                ssh_manager,
                server,
                bool(prerequisites["maps_file_exists"]),
            )
            if request.expected_revision and request.expected_revision != content_revision(
                current_content
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="maps.txt changed on the server. Reload it before saving.",
                )
            await _replace_maps_config(ssh_manager, server, request.content)
            prerequisites["maps_file_exists"] = True
            return {
                **_config_payload(
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
    server = await get_server_with_permission(server_id, current_user, db)
    async with maintenance_lock_service.get(server_id, operation="map_preset", wait=False):
        ssh_manager = await _connect(server)
        try:
            prerequisites = await _inspect_prerequisites(ssh_manager, server)
            _require_prerequisites(prerequisites)
            current_maps_content, _ = await _read_maps_config(
                ssh_manager,
                server,
                bool(prerequisites["maps_file_exists"]),
            )
            if request.expected_revision != content_revision(current_maps_content):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="maps.txt changed on the server. Reload it before switching presets.",
                )

            if request.preset == "official":
                updated_maps_content = await _official_maps_config(ssh_manager, server)
            else:
                updated_maps_content = await _remote_maps_config(request.preset)

            plugin_config_payload: Optional[dict[str, object]] = None
            if request.preset == "kz":
                current_plugin_content, plugin_file_exists = await _read_plugin_config(
                    ssh_manager,
                    server,
                    bool(prerequisites["plugin_config_file_exists"]),
                )
                if (
                    request.plugin_config_expected_revision
                    and request.plugin_config_expected_revision
                    != content_revision(current_plugin_content)
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "MapChooser config.json changed on the server. "
                            "Reload it before switching to the KZ preset."
                        ),
                    )
                try:
                    updated_plugin_content = update_plugin_config(
                        current_plugin_content,
                        KZ_PLUGIN_CONFIG,
                        allow_missing_known_fields=True,
                    )
                except PluginConfigError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail=f"Unable to apply the KZ MapChooser settings: {exc}",
                    ) from exc

                await _replace_plugin_config(ssh_manager, server, updated_plugin_content)
                prerequisites["plugin_config_file_exists"] = True
                plugin_config_payload = _plugin_config_payload(
                    updated_plugin_content,
                    config_file_exists=True,
                    prerequisites=prerequisites,
                )
                if not plugin_file_exists:
                    logger.info("Created MapChooser config.json while applying KZ preset")

            await _replace_maps_config(ssh_manager, server, updated_maps_content)
            prerequisites["maps_file_exists"] = True
            maps_payload = _config_payload(
                updated_maps_content,
                maps_file_exists=True,
                prerequisites=prerequisites,
            )
            return {
                **maps_payload,
                "preset": request.preset,
                "map_count": len(maps_payload["maps"]),
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
    server = await get_server_with_permission(server_id, current_user, db)
    async with maintenance_lock_service.get(server_id, operation="map_update", wait=False):
        ssh_manager = await _connect(server)
        try:
            prerequisites = await _inspect_prerequisites(ssh_manager, server)
            _require_prerequisites(prerequisites)
            try:
                workshop_id = normalize_workshop_id(request.workshop_id)
                restricted_times = validate_restricted_times(request.restricted_times)
            except MapConfigError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=str(exc),
                ) from exc
            content, _ = await _read_maps_config(
                ssh_manager,
                server,
                bool(prerequisites["maps_file_exists"]),
            )

            name = request.name.strip() if request.name else ""
            if not name:
                name = await _fetch_workshop_title(workshop_id) or ""
                if not name:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail="Unable to retrieve the Workshop title. Enter the map name manually and try again.",
                    )
            try:
                name = sanitize_map_name(name)
                updated_content = append_map_to_config(
                    content,
                    name=name,
                    workshop_id=workshop_id,
                    enabled=request.enabled,
                    min_players=request.min_players,
                    only_nominate=request.only_nominate,
                    restricted_times=restricted_times,
                )
            except MapConfigError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT
                    if "already exists" in str(exc)
                    else status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=str(exc),
                ) from exc

            await _replace_maps_config(ssh_manager, server, updated_content)
            prerequisites["maps_file_exists"] = True
            return {
                **_config_payload(
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
    server = await get_server_with_permission(server_id, current_user, db)
    async with maintenance_lock_service.get(server_id, operation="map_delete", wait=False):
        ssh_manager = await _connect(server)
        try:
            prerequisites = await _inspect_prerequisites(ssh_manager, server)
            _require_prerequisites(prerequisites)
            content, _ = await _read_maps_config(
                ssh_manager,
                server,
                bool(prerequisites["maps_file_exists"]),
            )
            if request.expected_revision != content_revision(content):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="maps.txt changed on the server. Reload it before changing this map.",
                )
            try:
                updated_content = set_map_enabled(
                    content,
                    name=request.name,
                    workshop_id=request.workshop_id,
                    enabled=request.enabled,
                )
            except MapConfigError as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND
                    if "was not found" in str(exc)
                    else status.HTTP_409_CONFLICT,
                    detail=str(exc),
                ) from exc

            await _replace_maps_config(ssh_manager, server, updated_content)
            prerequisites["maps_file_exists"] = True
            return {
                **_config_payload(
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
    server = await get_server_with_permission(server_id, current_user, db)
    async with maintenance_lock_service.get(server_id, operation="map_batch", wait=False):
        ssh_manager = await _connect(server)
        try:
            prerequisites = await _inspect_prerequisites(ssh_manager, server)
            _require_prerequisites(prerequisites)
            content, _ = await _read_maps_config(
                ssh_manager,
                server,
                bool(prerequisites["maps_file_exists"]),
            )
            if request.expected_revision != content_revision(content):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="maps.txt changed on the server. Reload it before deleting this map.",
                )
            try:
                updated_content = remove_map_from_config(
                    content,
                    name=request.name,
                    workshop_id=request.workshop_id,
                )
            except MapConfigError as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND
                    if "was not found" in str(exc)
                    else status.HTTP_409_CONFLICT,
                    detail=str(exc),
                ) from exc

            await _replace_maps_config(ssh_manager, server, updated_content)
            prerequisites["maps_file_exists"] = True
            return {
                **_config_payload(
                    updated_content,
                    maps_file_exists=True,
                    prerequisites=prerequisites,
                ),
                "message": f"Removed {request.name} from maps.txt",
            }
        finally:
            await ssh_manager.disconnect()
