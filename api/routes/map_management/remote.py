"""SSH and remote-file helpers for MapChooser management."""

from __future__ import annotations

import posixpath
import shlex
import uuid
from typing import Literal, Optional

from fastapi import HTTPException, status

from modules import Server
from services.compat import LateBoundModule
from services.ssh_manager import SSHManager

host = LateBoundModule("api.routes.map_management")


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
    ssh_manager = host.SSHManager()
    success, message = await ssh_manager.connect(server)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SSH connection failed: {message}",
        )
    return ssh_manager


async def _inspect_prerequisites(ssh_manager: SSHManager, server: Server) -> dict[str, object]:
    paths = host._remote_paths(server)
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
        "plugin_center_name": host.PLUGIN_CENTER_NAME,
        "plugin_center_url": host.PLUGIN_CENTER_URL,
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
                "plugin_center_name": host.PLUGIN_CENTER_NAME,
                "plugin_center_url": host.PLUGIN_CENTER_URL,
            },
        )


async def _read_maps_config(
    ssh_manager: SSHManager,
    server: Server,
    maps_file_exists: bool,
) -> tuple[str, bool]:
    if not maps_file_exists:
        return host.DEFAULT_MAPS_CONFIG, False
    maps_path = host._remote_paths(server)["maps"]
    success, content, error = await ssh_manager.read_file(
        maps_path,
        server,
        max_size=host.MAX_MAPS_CONFIG_BYTES,
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
        return host.DEFAULT_PLUGIN_CONFIG_CONTENT, False
    config_path = host._remote_paths(server)["config"]
    success, content, error = await ssh_manager.read_file(
        config_path,
        server,
        max_size=host.MAX_PLUGIN_CONFIG_BYTES,
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
    await host._replace_remote_config(
        ssh_manager,
        server,
        host._remote_paths(server)["maps"],
        content,
        "maps.txt",
    )


async def _replace_plugin_config(
    ssh_manager: SSHManager,
    server: Server,
    content: str,
) -> None:
    await host._replace_remote_config(
        ssh_manager,
        server,
        host._remote_paths(server)["config"],
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
        parsed = host.parse_maps_config(content)
    except host.MapConfigError as exc:
        parsed_maps: list[dict[str, object]] = []
        config_error = f"Invalid maps.txt: {exc}"
    else:
        parsed_maps = parsed.maps
    return {
        **prerequisites,
        "maps_file_exists": maps_file_exists,
        "content": content,
        "revision": host.content_revision(content),
        "maps": parsed_maps,
        "config_error": config_error,
    }


def _map_count(payload: dict[str, object]) -> int:
    """Return the number of parsed maps without trusting unvalidated payload data."""
    maps = payload.get("maps")
    return len(maps) if isinstance(maps, list) else 0


def _plugin_config_payload(
    content: str,
    *,
    config_file_exists: bool,
    prerequisites: dict[str, object],
) -> dict[str, object]:
    config_error: Optional[str] = None
    try:
        config = host.parse_plugin_config(content)
        fields, unsupported_fields = host.build_plugin_config_fields(config)
    except host.PluginConfigError as exc:
        fields = []
        unsupported_fields = []
        config_error = f"Invalid MapChooser config.json: {exc}"
    return {
        **prerequisites,
        "plugin_config_file_exists": config_file_exists,
        "revision": host.content_revision(content),
        "fields": fields,
        "unsupported_fields": unsupported_fields,
        "config_error": config_error,
    }


async def _fetch_workshop_title(workshop_id: str) -> Optional[str]:
    success, data, error = await host.http_helper.post(
        "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/",
        headers={"User-Agent": "UpKK-CS2-ServerManager"},
        data={"itemcount": "1", "publishedfileids[0]": workshop_id},
        timeout=10,
    )
    if not success or not isinstance(data, dict):
        host.logger.warning("Unable to resolve Workshop title for %s: %s", workshop_id, error)
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
    maps_directory = shlex.quote(host._remote_paths(server)["game_maps"])
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
        return host.render_official_maps_config(map_names)
    except host.MapConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


async def _remote_maps_config(preset: Literal["kz", "ze"]) -> str:
    try:
        return await host.fetch_remote_map_pool(host.MAP_PRESET_URLS[preset])
    except host.RemoteMapPoolError as exc:
        host.logger.warning("Unable to fetch %s map preset: %s", preset, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to download the {preset.upper()} map preset: {exc}",
        ) from exc
