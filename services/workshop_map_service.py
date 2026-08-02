"""Two-phase, revision-checked Workshop map installation workflow."""

from __future__ import annotations

import hashlib
import json
import posixpath
import shlex
import uuid
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from modules import MarketPlugin, Server, User
from modules.http_helper import http_helper
from services.maintenance_lock import maintenance_lock_service
from services.map_management_service import (
    DEFAULT_MAPS_CONFIG,
    DEFAULT_PLUGIN_CONFIG_CONTENT,
    MAX_MAPS_CONFIG_BYTES,
    MAX_PLUGIN_CONFIG_BYTES,
    append_map_to_config,
    content_revision,
    normalize_workshop_id,
    parse_maps_config,
    parse_plugin_config,
    sanitize_map_name,
    update_plugin_config,
    validate_restricted_times,
)
from services.plugin_auto_update_service import record_framework_installation
from services.plugin_conflict_service import (
    _emit_plan_progress,
    build_plugin_install_plan,
    execute_plugin_install_plan,
)
from services.ssh_manager import SSHManager

ProgressCallback = Callable[..., Awaitable[None]]
MAPCHOOSER_MARKET_TITLE = "CS2-Upkk-PanelPLG-Mapchooser"


class WorkshopPlanError(ValueError):
    """Raised when the Steam item or server preflight is invalid."""


def _paths(server: Server) -> dict[str, str]:
    csgo = posixpath.join(server.game_directory.rstrip("/"), "cs2/game/csgo")
    css = posixpath.join(csgo, "addons/counterstrikesharp")
    return {
        "metamod": posixpath.join(csgo, "addons/metamod"),
        "css": css,
        "css_bin": posixpath.join(css, "bin"),
        "plugins": posixpath.join(css, "plugins"),
        "mapchooser_dll": posixpath.join(css, "plugins/MapChooser/MapChooser.dll"),
        "maps": posixpath.join(css, "configs/plugins/MapChooser/maps.txt"),
        "config": posixpath.join(css, "configs/plugins/MapChooser/config.json"),
    }


async def fetch_workshop_details(workshop_id_or_url: str) -> dict[str, Any]:
    """Resolve and strictly validate one CS2 Workshop item via the Steam API."""
    workshop_id = normalize_workshop_id(workshop_id_or_url)
    success, data, error = await http_helper.post(
        "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/",
        headers={"User-Agent": "UpKK-CS2-ServerManager"},
        data={"itemcount": "1", "publishedfileids[0]": workshop_id},
        timeout=15,
    )
    if not success or not isinstance(data, dict):
        raise WorkshopPlanError(f"Unable to validate Workshop item: {error}")
    response = data.get("response")
    details = response.get("publishedfiledetails") if isinstance(response, dict) else None
    item = details[0] if isinstance(details, list) and details else None
    if not isinstance(item, dict):
        raise WorkshopPlanError("Workshop item does not exist or is unavailable")
    try:
        result_code = int(item.get("result") or 0)
        consumer_app_id = int(item.get("consumer_app_id") or 0)
    except (TypeError, ValueError) as exc:
        raise WorkshopPlanError("Workshop item returned invalid Steam metadata") from exc
    if result_code != 1:
        raise WorkshopPlanError("Workshop item does not exist or is unavailable")
    if consumer_app_id != 730:
        raise WorkshopPlanError("Workshop item is not a Counter-Strike 2 item")
    if str(item.get("banned") or "0").casefold() in {"1", "true", "yes"}:
        raise WorkshopPlanError("Workshop item is disabled or banned")
    title = str(item.get("title") or "").strip()
    if not title:
        raise WorkshopPlanError("Workshop item has no usable title")
    return {
        "workshop_id": workshop_id,
        "title": sanitize_map_name(title),
        "consumer_app_id": 730,
    }


async def _connect(server: Server) -> SSHManager:
    manager = SSHManager()
    success, message = await manager.connect(server)
    if not success:
        raise WorkshopPlanError(f"SSH connection failed: {message}")
    return manager


async def _inspect(manager: SSHManager, server: Server) -> dict[str, bool]:
    paths = _paths(server)
    command = (
        f"if test -d {shlex.quote(paths['metamod'])}; then echo metamod=1; else echo metamod=0; fi; "
        f"if test -d {shlex.quote(paths['css'])} && find {shlex.quote(paths['css_bin'])} "
        "-maxdepth 5 -type f \\( -name CounterStrikeSharp.API.dll -o -name counterstrikesharp.so "
        "-o -name CounterStrikeSharp.dll \\) -print -quit 2>/dev/null | grep -q .; "
        "then echo css=1; else echo css=0; fi; "
        f"if test -f {shlex.quote(paths['mapchooser_dll'])} || "
        f"find {shlex.quote(paths['plugins'])} -maxdepth 4 -type f -name MapChooser.dll "
        "-print -quit 2>/dev/null | grep -q .; then echo mapchooser=1; else echo mapchooser=0; fi; "
        f"if test -f {shlex.quote(paths['maps'])}; then echo maps=1; else echo maps=0; fi; "
        f"if test -f {shlex.quote(paths['config'])}; then echo config=1; else echo config=0; fi"
    )
    success, stdout, stderr = await manager.execute_command(command, timeout=20)
    if not success:
        raise WorkshopPlanError(stderr or stdout or "Prerequisite inspection failed")
    values: dict[str, bool] = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value.strip() == "1"
    return values


async def _read_configs(
    manager: SSHManager, server: Server, state: dict[str, bool]
) -> tuple[str, str]:
    paths = _paths(server)
    maps = DEFAULT_MAPS_CONFIG
    config = DEFAULT_PLUGIN_CONFIG_CONTENT
    if state.get("maps"):
        success, maps, error = await manager.read_file(
            paths["maps"], server, max_size=MAX_MAPS_CONFIG_BYTES
        )
        if not success:
            raise WorkshopPlanError(f"Unable to read maps.txt: {error}")
    if state.get("config"):
        success, config, error = await manager.read_file(
            paths["config"], server, max_size=MAX_PLUGIN_CONFIG_BYTES
        )
        if not success:
            raise WorkshopPlanError(f"Unable to read MapChooser config: {error}")
    return maps, config


def _plan_hash(plan: dict[str, Any]) -> str:
    payload = {key: value for key, value in plan.items() if key != "plan_hash"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


async def _find_mapchooser(db: AsyncSession) -> MarketPlugin | None:
    plugins, _ = await MarketPlugin.search_plugins(
        db, search_query=MAPCHOOSER_MARKET_TITLE, limit=20
    )
    return next(
        (item for item in plugins if item.title.casefold() == MAPCHOOSER_MARKET_TITLE.casefold()),
        None,
    )


async def build_workshop_map_plan(
    db: AsyncSession,
    server: Server,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Validate Steam metadata and create a revision-bound execution plan."""
    item = await fetch_workshop_details(str(request["workshop_id_or_url"]))
    name = sanitize_map_name(str(request.get("name") or item["title"]).strip())
    restricted_times = validate_restricted_times(str(request.get("restricted_times") or ""))
    manager = await _connect(server)
    try:
        state = await _inspect(manager, server)
        maps_content, config_content = await _read_configs(manager, server, state)
    finally:
        await manager.disconnect()

    # Parsing and appending during preflight detects duplicate IDs/names and
    # malformed existing files before a confirmation card is displayed.
    append_map_to_config(
        maps_content,
        name=name,
        workshop_id=item["workshop_id"],
        enabled=bool(request.get("enabled", True)),
        min_players=int(request.get("min_players", 0)),
        only_nominate=bool(request.get("only_nominate", False)),
        restricted_times=restricted_times,
    )
    config = parse_plugin_config(config_content)

    mapchooser = await _find_mapchooser(db)
    plugin_plan: dict[str, Any] | None = None
    if not state.get("mapchooser") and mapchooser and mapchooser.id:
        plugin_plan = await build_plugin_install_plan(db, server.id, mapchooser.id, server=server)

    steps: list[dict[str, Any]] = []
    if not state.get("metamod"):
        steps.append({"action": "install_framework", "framework": "metamod"})
    if not state.get("css"):
        steps.append({"action": "install_framework", "framework": "counterstrikesharp"})
    if not state.get("mapchooser"):
        steps.append(
            {
                "action": "install_market_plugin",
                "plugin_id": mapchooser.id if mapchooser else None,
                "title": MAPCHOOSER_MARKET_TITLE,
            }
        )
    if config.get("ChangeMapUse_host_workshop_map") is not True:
        steps.append(
            {
                "action": "patch_plugin_config",
                "setting": "ChangeMapUse_host_workshop_map",
                "value": True,
            }
        )
    steps.append(
        {
            "action": "append_map",
            "workshop_id": item["workshop_id"],
            "name": name,
        }
    )
    steps.append({"action": "verify"})

    hard_conflicts = plugin_plan["hard_conflicts"] if plugin_plan else []
    warnings = plugin_plan["warnings"] if plugin_plan else []
    blocking_reasons: list[str] = []
    if not state.get("mapchooser") and mapchooser is None:
        blocking_reasons.append(f"{MAPCHOOSER_MARKET_TITLE} is missing from the plugin market")
    if hard_conflicts:
        blocking_reasons.append("MapChooser installation has hard plugin conflicts")

    plan: dict[str, Any] = {
        "server_id": server.id,
        "workshop": {**item, "name": name},
        "settings": {
            "enabled": bool(request.get("enabled", True)),
            "min_players": int(request.get("min_players", 0)),
            "only_nominate": bool(request.get("only_nominate", False)),
            "restricted_times": restricted_times,
        },
        "current": state,
        "revisions": {
            "maps": content_revision(maps_content),
            "plugin_config": content_revision(config_content),
        },
        "plugin_plan": plugin_plan,
        "hard_conflicts": hard_conflicts,
        "warnings": warnings,
        "steps": steps,
        "blocked": bool(blocking_reasons),
        "blocking_reasons": blocking_reasons,
        "download_behavior": "Map is downloaded later by host_workshop_map; no predownload",
    }
    plan["plan_hash"] = _plan_hash(plan)
    return plan


async def _replace_with_backup(
    manager: SSHManager,
    server: Server,
    path: str,
    content: str,
    *,
    existed: bool,
) -> str | None:
    parent = posixpath.dirname(path)
    success, stdout, stderr = await manager.execute_command(
        f"mkdir -p -- {shlex.quote(parent)}", timeout=20
    )
    if not success:
        raise WorkshopPlanError(stderr or stdout or f"Unable to create {parent}")
    backup: str | None = None
    if existed:
        backup = f"{path}.ai-backup-{uuid.uuid4().hex}"
        success, stdout, stderr = await manager.execute_command(
            f"cp -p -- {shlex.quote(path)} {shlex.quote(backup)}", timeout=20
        )
        if not success:
            raise WorkshopPlanError(stderr or stdout or f"Unable to back up {path}")
    temporary = f"{path}.upkk-{uuid.uuid4().hex}.tmp"
    success, error = await manager.write_file(temporary, content, server)
    if not success:
        raise WorkshopPlanError(f"Unable to stage {path}: {error}")
    success, stdout, stderr = await manager.execute_command(
        f"mv -f -- {shlex.quote(temporary)} {shlex.quote(path)}", timeout=20
    )
    if not success:
        await manager.execute_command(f"rm -f -- {shlex.quote(temporary)}", timeout=10)
        raise WorkshopPlanError(stderr or stdout or f"Unable to replace {path}")
    return backup


async def execute_workshop_map_plan(
    db: AsyncSession,
    server: Server,
    user: User,
    request: dict[str, Any],
    acknowledged_warning_rule_ids: Iterable[int] = (),
    *,
    expected_plan_hash: str | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Revalidate and execute the exact confirmed plan, reporting partial work."""
    completed: list[dict[str, Any]] = []
    current_step: str | None = None

    async def report(step_id: str, step_status: str, message: str) -> None:
        nonlocal current_step
        if step_status == "running":
            current_step = step_id
        elif current_step == step_id:
            current_step = None
        await _emit_plan_progress(
            progress,
            message,
            step_id=step_id,
            step_status=step_status,
        )

    async with maintenance_lock_service.get(
        server.id, operation="workshop_map_plan", wait=False, ttl=3600
    ):
        current_server = (
            await Server.get_by_id(db, server.id)
            if user.is_admin
            else await Server.get_by_id_and_user(db, server.id, user.id)
        )
        if current_server is None:
            raise WorkshopPlanError("Server permission changed before execution")
        plan = await build_workshop_map_plan(db, current_server, request)
        if plan["blocked"]:
            raise WorkshopPlanError("; ".join(plan["blocking_reasons"]))
        if expected_plan_hash and plan["plan_hash"] != expected_plan_hash:
            raise WorkshopPlanError("Workshop plan changed; review and approve the new plan")

        acknowledged = {int(item) for item in acknowledged_warning_rule_ids}
        required = {int(item["rule_id"]) for item in plan["warnings"]}
        if required - acknowledged:
            raise WorkshopPlanError(
                "Missing warning acknowledgement(s): "
                + ", ".join(map(str, sorted(required - acknowledged)))
            )

        try:
            if not plan["current"].get("metamod"):
                await report("install_metamod", "running", "Installing Metamod")
                success, message = await SSHManager().install_metamod(current_server)
                if not success:
                    raise WorkshopPlanError(message)
                await record_framework_installation(current_server, user, "metamod")
                completed.append({"action": "install_metamod", "success": True})
                await report("install_metamod", "completed", "Installed Metamod")

            if not plan["current"].get("css"):
                await report(
                    "install_counterstrikesharp", "running", "Installing CounterStrikeSharp"
                )
                success, message = await SSHManager().install_counterstrikesharp(current_server)
                if not success:
                    raise WorkshopPlanError(message)
                await record_framework_installation(current_server, user, "counterstrikesharp")
                completed.append({"action": "install_counterstrikesharp", "success": True})
                await report(
                    "install_counterstrikesharp", "completed", "Installed CounterStrikeSharp"
                )

            if not plan["current"].get("mapchooser"):
                plugin_id = plan["plugin_plan"]["plugin"]["id"]
                await report("install_mapchooser", "running", "Installing MapChooser")

                async def mapchooser_progress(
                    message: str, message_type: str, metadata: dict[str, Any] | None = None
                ) -> None:
                    del message_type, metadata
                    await report("install_mapchooser", "running", message)

                plugin_result = await execute_plugin_install_plan(
                    db,
                    current_server,
                    user,
                    plugin_id,
                    acknowledged,
                    expected_plan_hash=plan["plugin_plan"]["plan_hash"],
                    progress=mapchooser_progress,
                    acquire_lock=False,
                )
                completed.append({"action": "install_mapchooser", "result": plugin_result})
                if not plugin_result["success"]:
                    raise WorkshopPlanError(plugin_result["message"])
                await report("install_mapchooser", "completed", "Installed MapChooser")

            manager = await _connect(current_server)
            try:
                state = await _inspect(manager, current_server)
                if not state.get("css") or not state.get("mapchooser"):
                    raise WorkshopPlanError("Prerequisite verification failed after installation")
                maps_content, config_content = await _read_configs(manager, current_server, state)
                if plan["current"].get("maps") and (
                    content_revision(maps_content) != plan["revisions"]["maps"]
                ):
                    raise WorkshopPlanError(
                        "maps.txt changed after planning; review the current file"
                    )
                if plan["current"].get("config") and (
                    content_revision(config_content) != plan["revisions"]["plugin_config"]
                ):
                    raise WorkshopPlanError(
                        "MapChooser config changed after planning; review the current file"
                    )

                if (
                    parse_plugin_config(config_content).get("ChangeMapUse_host_workshop_map")
                    is not True
                ):
                    await report(
                        "patch_plugin_config", "running", "Updating MapChooser configuration"
                    )
                    updated_config = update_plugin_config(
                        config_content,
                        {"ChangeMapUse_host_workshop_map": True},
                        allow_missing_known_fields=True,
                    )
                    backup = await _replace_with_backup(
                        manager,
                        current_server,
                        _paths(current_server)["config"],
                        updated_config,
                        existed=state.get("config", False),
                    )
                    config_content = updated_config
                    completed.append(
                        {
                            "action": "patch_plugin_config",
                            "success": True,
                            "backup": backup,
                        }
                    )
                    await report(
                        "patch_plugin_config", "completed", "Updated MapChooser configuration"
                    )

                await report("append_map", "running", "Adding map to MapChooser")
                updated_maps = append_map_to_config(
                    maps_content,
                    name=plan["workshop"]["name"],
                    workshop_id=plan["workshop"]["workshop_id"],
                    **plan["settings"],
                )
                maps_backup = await _replace_with_backup(
                    manager,
                    current_server,
                    _paths(current_server)["maps"],
                    updated_maps,
                    existed=state.get("maps", False),
                )
                completed.append({"action": "append_map", "success": True, "backup": maps_backup})
                await report("append_map", "completed", "Added map to MapChooser")

                # Final read-after-write verification checks both the exact ID
                # and the setting required for host_workshop_map downloads.
                await report("verify", "running", "Verifying Workshop map configuration")
                state = await _inspect(manager, current_server)
                verified_maps, verified_config = await _read_configs(manager, current_server, state)
                entries = parse_maps_config(verified_maps).maps
                matching = [
                    entry
                    for entry in entries
                    if str(entry.get("workshop_id")) == plan["workshop"]["workshop_id"]
                ]
                config_enabled = (
                    parse_plugin_config(verified_config).get("ChangeMapUse_host_workshop_map")
                    is True
                )
                if not matching or not config_enabled:
                    raise WorkshopPlanError("Final Workshop map verification failed")
                completed.append({"action": "verify", "success": True})
                await report("verify", "completed", "Verified Workshop map configuration")
            finally:
                await manager.disconnect()
        except Exception as exc:
            if current_step:
                await report(current_step, "failed", str(exc))
            return {
                "success": False,
                "message": str(exc),
                "completed": completed,
                "partial_completion": bool(completed),
            }

    return {
        "success": True,
        "message": f"Added {plan['workshop']['name']} to MapChooser",
        "workshop": plan["workshop"],
        "completed": completed,
        "predownloaded": False,
    }
