"""Backward-compatible facade for startup-setting domain rules."""

from modules.server_startup import (
    GAME_MODE_MAPPING,
    MANAGED_STARTUP_OPTIONS,
    normalize_additional_parameters,
    normalize_default_map,
    normalize_game_mode,
    normalize_game_type,
    resolved_game_mode,
)

__all__ = [
    "GAME_MODE_MAPPING",
    "MANAGED_STARTUP_OPTIONS",
    "normalize_additional_parameters",
    "normalize_default_map",
    "normalize_game_mode",
    "normalize_game_type",
    "resolved_game_mode",
]
