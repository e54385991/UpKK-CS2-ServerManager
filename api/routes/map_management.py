"""MapChooser map-pool management routes."""

from __future__ import annotations

import logging
import posixpath
import shlex
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.routes.servers import get_server_with_permission
from modules import Server, User, get_current_active_user, get_db
from modules.http_helper import http_helper
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
    sanitize_map_name,
    set_map_enabled,
    update_plugin_config,
    validate_restricted_times,
)
from services.maintenance_lock import maintenance_lock_service
from services.ssh_manager import SSHManager


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/servers/{server_id}/maps", tags=["map-management"])

PLUGIN_CENTER_NAME = "CS2-Upkk-PanelPLG-Mapchooser"
PLUGIN_CENTER_URL = "/plugin-market?search=CS2-Upkk-PanelPLG-Mapchooser"
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


def _remote_paths(server: Server) -> dict[str, str]:
    csgo_dir = posixpath.join(server.game_directory.rstrip("/"), "cs2/game/csgo")
    css_dir = posixpath.join(csgo_dir, "addons/counterstrikesharp")
    return {
        "counterstrikesharp": css_dir,
        "plugins": posixpath.join(css_dir, "plugins"),
        "mapchooser_dll": posixpath.join(css_dir, "plugins/MapChooser/MapChooser.dll"),
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


@router.get("/status")
async def get_map_management_status(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, object]:
    server = await get_server_with_permission(server_id, current_user, db)
    ssh_manager = await _connect(server)
    try:
        return await _inspect_prerequisites(ssh_manager, server)
    finally:
        await ssh_manager.disconnect()


@router.get("/plugin-config")
async def get_plugin_config(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
            if (
                request.expected_revision
                and request.expected_revision != content_revision(current_content)
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
            if (
                request.expected_revision
                and request.expected_revision != content_revision(current_content)
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


@router.post("")
async def add_map(
    server_id: int,
    request: MapAddRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
