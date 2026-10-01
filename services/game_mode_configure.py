"""Focused lifecycle stages with explicit session inputs."""

# ruff: noqa: F403,F405

from __future__ import annotations
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Protocol
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select
from modules import ManagedPlugin, Server, ServerStatus, User
from services.game_mode_execstack import (
    append_execstack_step,
    run_planned_execstack_step,
)
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
    read_linux_release,
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
from services.server_compatibility import effective_clear_execstack
from services.ssh_manager import SSHManager

from services.game_mode_types import PlanReport
from services.compat import LateBoundModule

host = LateBoundModule('services.game_mode_install_service')

async def _configure_mode_server(db: AsyncSession, current_server: Server, user: User, plan: dict[str, Any], completed: list[dict[str, Any]], report: PlanReport, recipe: GameModeRecipe) -> None:
    manager = await host.connect(current_server)
    try:
        state = await host.inspect_game_mode_state(manager, current_server)
        if not state.get("css") or not state.get("mapchooser"):
            raise host.GameModePlanError("Prerequisite verification failed after installation")
        paths = host.remote_paths(current_server)
        maps_content = await host._read_text(
            manager,
            current_server,
            paths["maps"],
            exists=bool(state.get("maps")),
            default=host.DEFAULT_MAPS_CONFIG,
            max_size=host.MAX_MAPS_CONFIG_BYTES,
            label="maps.txt",
        )
        config_content = await host._read_text(
            manager,
            current_server,
            paths["config"],
            exists=bool(state.get("config")),
            default=host.DEFAULT_PLUGIN_CONFIG_CONTENT,
            max_size=host.MAX_PLUGIN_CONFIG_BYTES,
            label="MapChooser config.json",
        )
        if not state.get("config"):
            raise host.GameModePlanError(
                "MapChooser config.json was not generated after restart"
            )

        if host._config_needs_patch(config_content, recipe.plugin_config):
            await report(
                "patch_plugin_config",
                "running",
                "Updating MapChooser configuration",
            )
            updated_config = host.update_plugin_config(
                config_content,
                recipe.plugin_config,
                allow_missing_known_fields=True,
            )
            backup = await host.replace_remote_file(
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
            if host._map_already_present(maps_content, item.workshop_id, item.name):
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
            updated_maps = host.append_map_to_config(
                maps_content,
                name=item.name,
                workshop_id=item.workshop_id,
            )
            maps_backup = await host.replace_remote_file(
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

        verified_config = host.parse_plugin_config(config_content)
        for key, desired in recipe.plugin_config.items():
            if verified_config.get(key) is not desired:
                raise host.GameModePlanError(f"MapChooser setting {key} was not applied")
        for item in recipe.maps_append:
            if not host._map_already_present(maps_content, item.workshop_id, item.name):
                raise host.GameModePlanError(
                    f"{item.name} ({item.workshop_id}) is missing from maps.txt"
                )
        completed.append({"action": "verify", "success": True})
    finally:
        await manager.disconnect()


