"""Pure planning helpers for game-mode operations."""

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from modules import MarketPlugin, Server
from services.game_mode_recipes import list_recipes
from services.game_mode_remote import (
    GameModeRemoteError,
    connect,
    inspect_game_mode_state,
    resolve_addons_directory,
)
from services.map_management_service import (
    MapConfigError,
    parse_maps_config,
    parse_plugin_config,
)
from services.ssh_manager import SSHManager


class GameModePlanError(ValueError):
    """Raised when a game-mode plan cannot be built or executed."""


def _plan_hash(plan: dict[str, Any]) -> str:
    payload = {key: value for key, value in plan.items() if key != "plan_hash"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _jsonable(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _jsonable_dict(value: dict[str, Any]) -> dict[str, Any]:
    converted = _jsonable(value)
    return converted if isinstance(converted, dict) else {}


def _market_restart_required(
    titles: Iterable[str],
    presence: dict[str, bool],
    *,
    treat_as_missing: bool,
    base_required: bool,
) -> bool:
    if base_required or treat_as_missing:
        return True
    return any(not presence.get(title, False) for title in titles)


async def find_market_plugin_by_title(db: AsyncSession, title: str) -> MarketPlugin | None:
    plugins, _ = await MarketPlugin.search_plugins(db, search_query=title, limit=20)
    return next(
        (item for item in plugins if item.title.casefold() == title.casefold()),
        None,
    )


async def _read_text(
    manager: SSHManager,
    server: Server,
    path: str,
    *,
    exists: bool,
    default: str,
    max_size: int,
    label: str,
) -> str:
    if not exists:
        return default
    success, content, error = await manager.read_file(path, server, max_size=max_size)
    if not success:
        raise GameModePlanError(f"Unable to read {label}: {error}")
    return content


def _map_already_present(maps_content: str, workshop_id: str, name: str) -> bool:
    try:
        entries = parse_maps_config(maps_content).maps
    except MapConfigError:
        return False
    return any(
        str(item.get("workshop_id")) == workshop_id
        or str(item.get("name") or "").casefold() == name.casefold()
        for item in entries
    )


def _config_needs_patch(config_content: str, desired: dict[str, object]) -> bool:
    parsed = parse_plugin_config(config_content)
    return any(parsed.get(key) is not desired_value for key, desired_value in desired.items())


async def catalog_for_server(
    db: AsyncSession,
    server: Server,
    *,
    plugin_finder: Any = find_market_plugin_by_title,
) -> dict[str, Any]:
    """Return recipe metadata plus a best-effort live presence snapshot."""
    addons_path = resolve_addons_directory(server.game_directory)
    state: dict[str, bool] | None = None
    reachable = True
    if getattr(server, "is_ssh_down", False):
        reachable = False
    else:
        try:
            manager = await connect(server)
            try:
                state = await inspect_game_mode_state(manager, server)
            finally:
                await manager.disconnect()
        except GameModeRemoteError, GameModePlanError:
            reachable = False

    modes: list[dict[str, Any]] = []
    for recipe in list_recipes():
        present = {
            "counterstrikesharp": None if state is None else bool(state.get("css")),
            "cs2kz-metamod": None if state is None else bool(state.get("cs2kz")),
            "mapchooser": None if state is None else bool(state.get("mapchooser")),
        }
        missing = [
            title for title in recipe.market_plugin_titles if await plugin_finder(db, title) is None
        ]
        modes.append(
            {
                "id": recipe.id,
                "launch_upsert": dict(recipe.launch_upsert),
                "frameworks": list(recipe.frameworks),
                "market_plugin_titles": list(recipe.market_plugin_titles),
                "maps": [
                    {"name": item.name, "workshop_id": item.workshop_id}
                    for item in recipe.maps_append
                ],
                "plugin_config": dict(recipe.plugin_config),
                "startup_workshop_map": recipe.startup_workshop_map,
                "present": present,
                "missing_market_plugins": missing,
            }
        )
    return {
        "server_id": server.id,
        "reachable": reachable,
        "additional_parameters": server.additional_parameters,
        "addons_path": addons_path,
        "addons_present": None if state is None else bool(state.get("addons")),
        "swiftly_installed": None if state is None else bool(state.get("swiftly")),
        "modes": modes,
    }
