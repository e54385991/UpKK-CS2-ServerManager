"""Focused lifecycle stages with explicit session inputs."""

# ruff: noqa: F403,F405

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from modules import Server, User
from services.compat import LateBoundModule
from services.game_mode_recipes import (
    GameModeRecipe,
)
from services.game_mode_types import PlanReport

host = LateBoundModule("services.game_mode_install_service")


async def _configure_mode_server(
    db: AsyncSession,
    current_server: Server,
    user: User,
    plan: dict[str, Any],
    completed: list[dict[str, Any]],
    report: PlanReport,
    recipe: GameModeRecipe,
) -> None:
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
            raise host.GameModePlanError("MapChooser config.json was not generated after restart")

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
