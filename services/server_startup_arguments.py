"""Validation and shell-safe serialization for CS2 startup settings."""

from __future__ import annotations

import re
import shlex

GAME_MODE_MAPPING: dict[str, tuple[str, str]] = {
    "casual": ("0", "0"),
    "competitive": ("0", "1"),
    "wingman": ("0", "2"),
    "arms_race": ("1", "0"),
    "demolition": ("1", "1"),
    "deathmatch": ("2", "0"),
    "custom": ("3", "0"),
}

_GAME_MODE_ALIASES = {"armsrace": "arms_race"}
_MAP_NAME = re.compile(r"[A-Za-z0-9_./-]+")
_OPTION_NAME = re.compile(r"[+-][A-Za-z_][A-Za-z0-9_]*")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_SHELL_OPERATOR = re.compile(r"[;&|<>`$]")
_SHELL_EXPANSION = re.compile(r"[*?\[\]~]")

# These are emitted from dedicated, validated panel fields. Allowing a second
# copy in additional_parameters would make the effective configuration
# ambiguous and could bypass the approval diff shown to the user.
MANAGED_STARTUP_OPTIONS = frozenset(
    {
        "clientport",
        "game_mode",
        "game_type",
        "hostname",
        "ip",
        "map",
        "maxplayers",
        "port",
        "rcon_password",
        "sv_password",
        "sv_setsteamaccount",
        "tv_enable",
        "tv_name",
        "tv_port",
    }
)


def normalize_default_map(value: str) -> str:
    """Return a bounded map identifier which is safe as one command argument."""
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("Default map cannot be empty")
    if len(normalized) > 100 or _MAP_NAME.fullmatch(normalized) is None:
        raise ValueError(
            "Default map may contain only letters, numbers, underscores, dots, slashes, and hyphens"
        )
    if any(part == ".." for part in normalized.split("/")):
        raise ValueError("Default map cannot contain parent-directory segments")
    return normalized


def normalize_game_mode(value: str) -> str:
    """Normalize a supported named mode or a bounded numeric game_mode value."""
    normalized = str(value).strip().casefold()
    normalized = _GAME_MODE_ALIASES.get(normalized, normalized)
    if normalized in GAME_MODE_MAPPING:
        return normalized
    if normalized.isdigit() and 0 <= int(normalized) <= 9:
        return normalized
    raise ValueError(
        "Game mode must be casual, competitive, wingman, arms_race, demolition, "
        "deathmatch, custom, or a numeric value from 0 to 9"
    )


def normalize_game_type(value: str) -> str:
    """Normalize a bounded numeric game_type value."""
    normalized = str(value).strip()
    if not normalized.isdigit() or not 0 <= int(normalized) <= 9:
        raise ValueError("Game type must be a numeric value from 0 to 9")
    return normalized


def normalize_additional_parameters(value: str | None) -> str | None:
    """Parse CS2 options and return a shell-safe, deterministic representation.

    Additional parameters are deliberately restricted to Source-style options
    such as ``+sv_hibernate_when_empty 0`` or ``-insecure``. They are data for
    the CS2 process, never a general-purpose shell command.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if len(raw) > 4096:
        raise ValueError("Additional startup parameters must be at most 4096 characters")
    if _CONTROL_CHARACTER.search(raw):
        raise ValueError("Additional startup parameters cannot contain control characters")
    if _SHELL_OPERATOR.search(raw) or _SHELL_EXPANSION.search(raw):
        raise ValueError(
            "Additional startup parameters cannot contain shell operators or expansions"
        )

    try:
        tokens = shlex.split(raw, posix=True)
    except ValueError as exc:
        raise ValueError(f"Additional startup parameters have invalid quoting: {exc}") from exc
    if not tokens or len(tokens) > 128:
        raise ValueError("Additional startup parameters must contain between 1 and 128 tokens")

    saw_option = False
    for token in tokens:
        if len(token) > 512:
            raise ValueError("Each additional startup argument must be at most 512 characters")
        if _OPTION_NAME.fullmatch(token):
            saw_option = True
            option_name = token[1:].casefold()
            if option_name in MANAGED_STARTUP_OPTIONS:
                raise ValueError(
                    f"{token} is managed by a dedicated server setting and cannot be duplicated"
                )
            continue
        if not saw_option:
            raise ValueError(
                "Additional startup parameters must begin with a +parameter or -parameter"
            )

    # shlex.join quotes every token as needed, so the stored value can be
    # appended to the existing shell command without re-enabling expansion.
    return shlex.join(tokens)


def resolved_game_mode(game_mode: str, game_type: str | None) -> tuple[str, str]:
    """Resolve the displayed mode into the numeric pair accepted by CS2."""
    normalized_mode = normalize_game_mode(game_mode)
    if normalized_mode in GAME_MODE_MAPPING:
        mapped_type, mapped_mode = GAME_MODE_MAPPING[normalized_mode]
        return normalize_game_type(game_type) if game_type is not None else mapped_type, mapped_mode
    return normalize_game_type(game_type or "0"), normalized_mode
