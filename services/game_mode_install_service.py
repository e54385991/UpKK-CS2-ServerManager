"""Plan-then-execute orchestration for one-click game-mode installs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from modules import ManagedPlugin, Server, ServerStatus, User
from services.game_mode_launch import upsert_additional_parameters
from services.game_mode_planning import (
    GameModePlanError,
    _config_needs_patch,
    _jsonable_dict,
    _map_already_present,
    _market_restart_required,
    _plan_hash,
    _read_text,
    find_market_plugin_by_title,
)
from services.game_mode_planning import (
    catalog_for_server as _catalog_for_server,
)
from services.game_mode_recipes import (
    GameModeRecipe,
    UnknownGameModeError,
    get_recipe,
)
from services.game_mode_remote import (
    connect,
    inspect_game_mode_state,
    remote_paths,
    replace_remote_file,
    resolve_addons_directory,
    wait_file_paths,
    wait_for_remote_files,
    wipe_addons_directory,
)
from services.maintenance_lock import maintenance_lock_service
from services.map_management_service import (
    DEFAULT_MAPS_CONFIG,
    DEFAULT_PLUGIN_CONFIG_CONTENT,
    MAX_MAPS_CONFIG_BYTES,
    MAX_PLUGIN_CONFIG_BYTES,
    append_map_to_config,
    parse_plugin_config,
    update_plugin_config,
)
from services.plugin_auto_update_service import record_framework_installation
from services.plugin_conflict_service import (
    PluginPlanError,
    _emit_plan_progress,
    build_plugin_install_plan,
    execute_plugin_install_plan,
    validate_plugin_plan_acknowledgements,
)
from services.redis_manager import redis_manager
from services.ssh_manager import SSHManager

ProgressCallback = Callable[..., Awaitable[None]]

__all__ = [
    "GameModePlanError",
    "catalog_for_server",
    "build_game_mode_plan",
    "execute_game_mode_plan",
]


async def catalog_for_server(db: AsyncSession, server: Server) -> dict[str, Any]:
    """Compatibility wrapper that keeps monkeypatchable module-level finder."""
    return await _catalog_for_server(db, server, plugin_finder=find_market_plugin_by_title)


async def build_game_mode_plan(
    db: AsyncSession,
    server: Server,
    mode_id: str,
    *,
    wipe_addons: bool = False,
) -> dict[str, Any]:
    try:
        recipe = get_recipe(mode_id)
    except UnknownGameModeError as exc:
        raise GameModePlanError(f"Unknown game mode: {mode_id}") from exc

    addons_path = resolve_addons_directory(server.game_directory)
    manager = await connect(server)
    try:
        state = await inspect_game_mode_state(manager, server)
        maps_content = await _read_text(
            manager,
            server,
            remote_paths(server)["maps"],
            exists=bool(state.get("maps")),
            default=DEFAULT_MAPS_CONFIG,
            max_size=MAX_MAPS_CONFIG_BYTES,
            label="maps.txt",
        )
        config_content = await _read_text(
            manager,
            server,
            remote_paths(server)["config"],
            exists=bool(state.get("config")),
            default=DEFAULT_PLUGIN_CONFIG_CONTENT,
            max_size=MAX_PLUGIN_CONFIG_BYTES,
            label="MapChooser config.json",
        )
    finally:
        await manager.disconnect()

    launch_after = upsert_additional_parameters(server.additional_parameters, recipe.launch_upsert)
    launch_changed = (server.additional_parameters or None) != (launch_after or None)
    treat_as_missing = bool(wipe_addons)

    plugin_plans: dict[str, dict[str, Any]] = {}
    missing_titles: list[str] = []
    hard_conflicts: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for title in recipe.market_plugin_titles:
        plugin = await find_market_plugin_by_title(db, title)
        if plugin is None or plugin.id is None:
            missing_titles.append(title)
            continue
        try:
            plan = await build_plugin_install_plan(
                db, server.id, int(plugin.id), include_dependencies=True, server=server
            )
        except PluginPlanError as exc:
            raise GameModePlanError(str(exc)) from exc
        plugin_plans[title] = plan
        hard_conflicts.extend(plan.get("hard_conflicts") or [])
        warnings.extend(plan.get("warnings") or [])

    steps: list[dict[str, Any]] = []
    mutations: list[dict[str, Any]] = []

    if wipe_addons:
        steps.append(
            {
                "id": "wipe_addons",
                "action": "wipe_addons",
                "status": "pending",
                "destructive": True,
                "path": addons_path,
            }
        )
        mutations.append(
            {
                "id": "wipe_addons",
                "target": addons_path,
                "before": "existing addons tree",
                "after": "empty addons directory",
                "destructive": True,
                "status": "pending",
            }
        )

    steps.append(
        {
            "id": "startup",
            "action": "upsert_launch_args",
            "status": "pending" if launch_changed else "unchanged",
        }
    )
    mutations.append(
        {
            "id": "startup",
            "target": "additional_parameters",
            "before": server.additional_parameters,
            "after": launch_after,
            "destructive": False,
            "status": "pending" if launch_changed else "unchanged",
        }
    )

    need_css = treat_as_missing or not state.get("css")
    if "counterstrikesharp" in recipe.frameworks:
        steps.append(
            {
                "id": "install_counterstrikesharp",
                "action": "install_framework",
                "framework": "counterstrikesharp",
                "status": "pending" if need_css else "already_present",
            }
        )
        mutations.append(
            {
                "id": "install_counterstrikesharp",
                "target": "CounterStrikeSharp (+ Metamod)",
                "before": "absent" if need_css else "installed",
                "after": "installed",
                "destructive": False,
                "status": "pending" if need_css else "already_present",
            }
        )

    title_presence = {
        "cs2kz-metamod": bool(state.get("cs2kz")),
        "CS2-Upkk-PanelPLG-Mapchooser": bool(state.get("mapchooser")),
    }
    for title in recipe.market_plugin_titles:
        present = title_presence.get(title, False) and not treat_as_missing
        plugin_plan = plugin_plans.get(title)
        steps.append(
            {
                "id": f"install:{title}",
                "action": "install_market_plugin",
                "title": title,
                "plugin_id": plugin_plan["plugin"]["id"] if plugin_plan else None,
                "status": "pending" if not present else "already_present",
            }
        )
        mutations.append(
            {
                "id": f"install:{title}",
                "target": title,
                "before": "absent" if not present else "installed",
                "after": "installed",
                "destructive": False,
                "status": "pending" if not present else "already_present",
            }
        )

    need_restart = _market_restart_required(
        recipe.market_plugin_titles,
        title_presence,
        treat_as_missing=treat_as_missing,
        base_required=need_css
        or not state.get("config")
        or not state.get("maps")
        or launch_changed,
    )
    steps.append(
        {
            "id": "restart_and_wait",
            "action": "restart_and_wait_configs",
            "status": "pending" if need_restart else "unchanged",
            "files": list(recipe.wait_files),
        }
    )
    mutations.append(
        {
            "id": "restart_and_wait",
            "target": "game process + MapChooser configs",
            "before": "current process",
            "after": "restarted; wait for generated config.json and maps.txt",
            "destructive": False,
            "status": "pending" if need_restart else "unchanged",
        }
    )

    config_patch = _config_needs_patch(config_content, recipe.plugin_config) or treat_as_missing
    steps.append(
        {
            "id": "patch_plugin_config",
            "action": "patch_plugin_config",
            "values": dict(recipe.plugin_config),
            "status": "pending" if config_patch else "unchanged",
        }
    )
    mutations.append(
        {
            "id": "patch_plugin_config",
            "target": "MapChooser config.json",
            "before": {
                key: parse_plugin_config(config_content).get(key) for key in recipe.plugin_config
            },
            "after": dict(recipe.plugin_config),
            "destructive": False,
            "status": "pending" if config_patch else "unchanged",
        }
    )

    for item in recipe.maps_append:
        already = (not treat_as_missing) and _map_already_present(
            maps_content, item.workshop_id, item.name
        )
        steps.append(
            {
                "id": f"append_map:{item.workshop_id}",
                "action": "append_map",
                "name": item.name,
                "workshop_id": item.workshop_id,
                "status": "already_present" if already else "pending",
            }
        )
        mutations.append(
            {
                "id": f"append_map:{item.workshop_id}",
                "target": f"{item.name} ({item.workshop_id})",
                "before": "in pool" if already else "absent",
                "after": "in MapChooser pool",
                "destructive": False,
                "status": "already_present" if already else "pending",
            }
        )

    blocking_reasons: list[str] = []
    if missing_titles:
        blocking_reasons.append("Missing from the plugin market: " + ", ".join(missing_titles))
    if hard_conflicts:
        blocking_reasons.append("Installation is blocked by a hard plugin conflict")
    if state.get("swiftly") and need_css and not wipe_addons:
        blocking_reasons.append(
            "SwiftlyS2 is installed and conflicts with CounterStrikeSharp. "
            "Enable a clean addons wipe or remove SwiftlyS2 first."
        )

    plan: dict[str, Any] = {
        "server_id": server.id,
        "mode_id": recipe.id,
        "wipe_addons": bool(wipe_addons),
        "addons_path": addons_path,
        "current": state,
        "startup": {
            "before": server.additional_parameters,
            "after": launch_after,
            "changed": launch_changed,
        },
        "plugin_config": dict(recipe.plugin_config),
        "maps": [
            {"name": item.name, "workshop_id": item.workshop_id} for item in recipe.maps_append
        ],
        "wait_files": list(recipe.wait_files),
        "plugin_plans": plugin_plans,
        "hard_conflicts": hard_conflicts,
        "warnings": warnings,
        "steps": steps,
        "mutations": mutations,
        "blocked": bool(blocking_reasons),
        "blocking_reasons": blocking_reasons,
    }
    plan["plan_hash"] = _plan_hash(_jsonable_dict(plan))
    return plan


async def _clear_managed_plugins(db: AsyncSession, server_id: int) -> int:
    result = await db.execute(
        select(ManagedPlugin).where(col(ManagedPlugin.server_id) == server_id)
    )
    rows = list(result.scalars().all())
    if not rows:
        await db.execute(delete(ManagedPlugin).where(col(ManagedPlugin.server_id) == server_id))
        await db.commit()
        return 0
    await db.execute(delete(ManagedPlugin).where(col(ManagedPlugin.server_id) == server_id))
    await db.commit()
    return len(rows)


async def _save_launch_args(db: AsyncSession, server: Server, value: str | None) -> None:
    server.additional_parameters = value
    db.add(server)
    await db.commit()
    await redis_manager.clear_server_cache(int(server.id))


async def execute_game_mode_plan(
    db: AsyncSession,
    server: Server,
    user: User,
    mode_id: str,
    *,
    wipe_addons: bool,
    expected_plan_hash: str,
    acknowledged_warning_rule_ids: Iterable[int] = (),
    progress: ProgressCallback | None = None,
    operation_id: str | None = None,
) -> dict[str, Any]:
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
        server.id, operation="game_mode_install", wait=False, ttl=7200
    ):
        current_server = (
            await Server.get_by_id(db, server.id)
            if user.is_admin
            else await Server.get_by_id_and_user(db, server.id, user.id)
        )
        if current_server is None:
            raise GameModePlanError("Server permission changed before execution")
        plan = await build_game_mode_plan(db, current_server, mode_id, wipe_addons=wipe_addons)
        if plan["blocked"]:
            raise GameModePlanError("; ".join(plan["blocking_reasons"]))
        if plan["plan_hash"] != expected_plan_hash:
            raise GameModePlanError("Game-mode plan changed; review and approve the new plan")
        acknowledged = {int(item) for item in acknowledged_warning_rule_ids}
        required = {int(item["rule_id"]) for item in plan["warnings"]}
        if required - acknowledged:
            raise GameModePlanError(
                "Missing warning acknowledgement(s): "
                + ", ".join(map(str, sorted(required - acknowledged)))
            )

        recipe: GameModeRecipe = get_recipe(mode_id)
        try:
            if wipe_addons:
                await report("wipe_addons", "running", f"Wiping {plan['addons_path']}")
                restart_manager = SSHManager()
                stopped, stop_message = await restart_manager.stop_server(current_server)
                if not stopped:
                    raise GameModePlanError(
                        f"Unable to stop the server before wiping addons: {stop_message}"
                    )
                manager = await connect(current_server)
                try:
                    await wipe_addons_directory(manager, plan["addons_path"])
                finally:
                    await manager.disconnect()
                cleared = await _clear_managed_plugins(db, int(current_server.id))
                completed.append(
                    {"action": "wipe_addons", "success": True, "cleared_tracking": cleared}
                )
                await report("wipe_addons", "completed", "Addons directory wiped")
                current_server = (
                    await Server.get_by_id(db, server.id)
                    if user.is_admin
                    else await Server.get_by_id_and_user(db, server.id, user.id)
                )
                if current_server is None:
                    raise GameModePlanError("Server disappeared after addons wipe")

            if plan["startup"]["changed"]:
                await report("startup", "running", "Updating launch parameters")
                await _save_launch_args(db, current_server, plan["startup"]["after"])
                completed.append({"action": "upsert_launch_args", "success": True})
                await report("startup", "completed", "Launch parameters saved")

            need_css = wipe_addons or not plan["current"].get("css")
            if need_css and "counterstrikesharp" in recipe.frameworks:

                async def css_progress(message: str) -> None:
                    await report("install_counterstrikesharp", "running", message)

                await report(
                    "install_counterstrikesharp",
                    "running",
                    "Installing CounterStrikeSharp (includes Metamod)",
                )
                success, message = await SSHManager().install_counterstrikesharp(
                    current_server, css_progress
                )
                if not success:
                    raise GameModePlanError(message)
                await record_framework_installation(current_server, user, "counterstrikesharp")
                completed.append({"action": "install_counterstrikesharp", "success": True})
                await report(
                    "install_counterstrikesharp",
                    "completed",
                    "Installed CounterStrikeSharp",
                )

            for title in recipe.market_plugin_titles:
                plugin_plan = plan["plugin_plans"].get(title)
                if plugin_plan is None:
                    raise GameModePlanError(f"{title} is missing from the plugin market")
                present = False
                if not wipe_addons:
                    if title == "cs2kz-metamod":
                        present = bool(plan["current"].get("cs2kz"))
                    elif title == "CS2-Upkk-PanelPLG-Mapchooser":
                        present = bool(plan["current"].get("mapchooser"))
                if present:
                    completed.append(
                        {"action": f"install:{title}", "success": True, "skipped": True}
                    )
                    continue

                async def plugin_progress(
                    message: str,
                    _kind: str = "status",
                    _metadata: dict[str, Any] | None = None,
                    *,
                    step_title: str = title,
                ) -> None:
                    await report(f"install:{step_title}", "running", message)

                await report(f"install:{title}", "running", f"Installing {title}")
                try:
                    validate_plugin_plan_acknowledgements(plugin_plan, acknowledged)
                except PluginPlanError as exc:
                    raise GameModePlanError(str(exc)) from exc
                result = await execute_plugin_install_plan(
                    db,
                    current_server,
                    user,
                    int(plugin_plan["plugin"]["id"]),
                    acknowledged,
                    expected_plan_hash=None if wipe_addons else plugin_plan.get("plan_hash"),
                    progress=plugin_progress,
                    acquire_lock=False,
                    operation_id=operation_id,
                    include_dependencies=True,
                )
                completed.append({"action": f"install:{title}", "result": result})
                if not result.get("success"):
                    raise GameModePlanError(
                        str(result.get("message") or f"Failed to install {title}")
                    )
                await report(f"install:{title}", "completed", f"Installed {title}")

            restart_step = next(item for item in plan["steps"] if item["id"] == "restart_and_wait")
            if restart_step["status"] == "pending" or wipe_addons:
                await report(
                    "restart_and_wait",
                    "running",
                    "Restarting the server and waiting for generated configs",
                )

                async def restart_progress(message: str) -> None:
                    await report("restart_and_wait", "running", message)

                restart_manager = SSHManager()
                stopped, stop_message = await restart_manager.stop_server(current_server)
                if not stopped:
                    current_server.status = ServerStatus.ERROR
                    db.add(current_server)
                    await db.commit()
                    raise GameModePlanError(
                        f"Unable to stop server before plugin initialization: {stop_message}"
                    )
                started, start_message = await restart_manager.start_server(
                    current_server, restart_progress
                )
                if not started:
                    current_server.status = ServerStatus.ERROR
                    db.add(current_server)
                    await db.commit()
                    raise GameModePlanError(
                        f"Unable to start server after plugin installation: {start_message}"
                    )
                current_server.status = ServerStatus.RUNNING
                db.add(current_server)
                await db.commit()

                manager = await connect(current_server)
                try:
                    await wait_for_remote_files(
                        manager,
                        wait_file_paths(current_server, recipe.wait_files),
                        progress=restart_progress,
                    )
                finally:
                    await manager.disconnect()
                completed.append({"action": "restart_and_wait", "success": True})
                await report(
                    "restart_and_wait",
                    "completed",
                    "Restarted and found generated MapChooser configs",
                )

            manager = await connect(current_server)
            try:
                state = await inspect_game_mode_state(manager, current_server)
                if not state.get("css") or not state.get("mapchooser"):
                    raise GameModePlanError("Prerequisite verification failed after installation")
                paths = remote_paths(current_server)
                maps_content = await _read_text(
                    manager,
                    current_server,
                    paths["maps"],
                    exists=bool(state.get("maps")),
                    default=DEFAULT_MAPS_CONFIG,
                    max_size=MAX_MAPS_CONFIG_BYTES,
                    label="maps.txt",
                )
                config_content = await _read_text(
                    manager,
                    current_server,
                    paths["config"],
                    exists=bool(state.get("config")),
                    default=DEFAULT_PLUGIN_CONFIG_CONTENT,
                    max_size=MAX_PLUGIN_CONFIG_BYTES,
                    label="MapChooser config.json",
                )
                if not state.get("config"):
                    raise GameModePlanError(
                        "MapChooser config.json was not generated after restart"
                    )

                if _config_needs_patch(config_content, recipe.plugin_config):
                    await report(
                        "patch_plugin_config",
                        "running",
                        "Updating MapChooser configuration",
                    )
                    updated_config = update_plugin_config(
                        config_content,
                        recipe.plugin_config,
                        allow_missing_known_fields=True,
                    )
                    backup = await replace_remote_file(
                        manager,
                        current_server,
                        paths["config"],
                        updated_config,
                        existed=True,
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
                        "patch_plugin_config",
                        "completed",
                        "Updated MapChooser configuration",
                    )

                for item in recipe.maps_append:
                    if _map_already_present(maps_content, item.workshop_id, item.name):
                        completed.append(
                            {
                                "action": f"append_map:{item.workshop_id}",
                                "success": True,
                                "skipped": True,
                            }
                        )
                        continue
                    await report(
                        f"append_map:{item.workshop_id}",
                        "running",
                        f"Adding {item.name} to MapChooser",
                    )
                    updated_maps = append_map_to_config(
                        maps_content,
                        name=item.name,
                        workshop_id=item.workshop_id,
                    )
                    maps_backup = await replace_remote_file(
                        manager,
                        current_server,
                        paths["maps"],
                        updated_maps,
                        existed=bool(state.get("maps")),
                    )
                    maps_content = updated_maps
                    completed.append(
                        {
                            "action": f"append_map:{item.workshop_id}",
                            "success": True,
                            "backup": maps_backup,
                        }
                    )
                    await report(
                        f"append_map:{item.workshop_id}",
                        "completed",
                        f"Added {item.name} to MapChooser",
                    )

                verified_config = parse_plugin_config(config_content)
                for key, desired in recipe.plugin_config.items():
                    if verified_config.get(key) is not desired:
                        raise GameModePlanError(f"MapChooser setting {key} was not applied")
                for item in recipe.maps_append:
                    if not _map_already_present(maps_content, item.workshop_id, item.name):
                        raise GameModePlanError(
                            f"{item.name} ({item.workshop_id}) is missing from maps.txt"
                        )
                completed.append({"action": "verify", "success": True})
            finally:
                await manager.disconnect()
        except Exception as exc:
            if current_step:
                await report(current_step, "failed", str(exc))
            installed = any(
                str(item.get("action") or "").startswith("install_")
                or str(item.get("action") or "").startswith("install:")
                or item.get("action") == "wipe_addons"
                for item in completed
            )
            restart_done = any(
                item.get("action") == "restart_and_wait" and item.get("success")
                for item in completed
            )
            return {
                "success": False,
                "message": str(exc),
                "completed": completed,
                "partial_completion": bool(completed),
                "restart_required": installed and not restart_done,
            }

    return {
        "success": True,
        "message": f"Installed the {mode_id} game mode",
        "mode_id": mode_id,
        "completed": completed,
        "restart_performed": any(
            item.get("action") == "restart_and_wait" and item.get("success") for item in completed
        ),
        "restart_required": False,
    }
