"""Declarative game-mode recipes. No SSH, no I/O."""

from __future__ import annotations

from dataclasses import dataclass

KZ_LIBSSL_DEB = "libssl1.1_1.1.1f-1ubuntu2.24_amd64.deb"
KZ_LIBSSL_URL = "https://security.ubuntu.com/ubuntu/pool/main/o/openssl/" + KZ_LIBSSL_DEB
KZ_LIBSSL_SHA256 = "7cf39d70a639017d1dd7c8d36daa2258063608688e449fddf40ffdd46f992a78"

# Keep in lockstep with api.routes.map_management.KZ_PLUGIN_CONFIG.
KZ_PLUGIN_CONFIG = {
    "UseGameTimeLimit": False,
    "EnforceTimeLimit": True,
    "ChangeMapUse_host_workshop_map": True,
}


@dataclass(frozen=True)
class GameModeMap:
    name: str
    workshop_id: str


@dataclass(frozen=True)
class GameModeRecipe:
    id: str
    launch_upsert: dict[str, str]
    frameworks: tuple[str, ...]
    market_plugin_titles: tuple[str, ...]
    wait_files: tuple[str, ...]
    plugin_config: dict[str, object]
    maps_append: tuple[GameModeMap, ...]
    startup_workshop_map: str
    system_dependencies: tuple[str, ...] = ()


KZ_RECIPE = GameModeRecipe(
    id="kz",
    launch_upsert={
        "+sv_hibernate_when_empty": "0",
        "+host_workshop_map": "3082213334",
        "-timeout": "120",
    },
    frameworks=("counterstrikesharp",),
    market_plugin_titles=(
        "cs2kz-metamod",
        "CS2-Upkk-PanelPLG-Mapchooser",
    ),
    wait_files=(
        "addons/counterstrikesharp/configs/plugins/MapChooser/config.json",
        "addons/counterstrikesharp/configs/plugins/MapChooser/maps.txt",
    ),
    plugin_config=dict(KZ_PLUGIN_CONFIG),
    maps_append=(GameModeMap(name="kz_variety", workshop_id="3250132197"),),
    startup_workshop_map="3082213334",
    system_dependencies=("libssl1.1",),
)

GAME_MODE_RECIPES: dict[str, GameModeRecipe] = {KZ_RECIPE.id: KZ_RECIPE}


class UnknownGameModeError(KeyError):
    """Raised when a mode id is not in the recipe catalog."""


def get_recipe(mode_id: str) -> GameModeRecipe:
    try:
        return GAME_MODE_RECIPES[mode_id]
    except KeyError as exc:
        raise UnknownGameModeError(mode_id) from exc


def list_recipes() -> list[GameModeRecipe]:
    return [GAME_MODE_RECIPES[key] for key in GAME_MODE_RECIPES]
