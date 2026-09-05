"""Patch schema for server settings."""

from __future__ import annotations

import re
from typing import List, Literal, Optional

from pydantic import Field, field_validator, model_validator
from sqlmodel import SQLModel

from modules.server_startup import (
    normalize_additional_parameters,
    normalize_default_map,
    normalize_game_mode,
    normalize_game_type,
)
from services.server_compatibility import normalize_execstack_targets

_APT_MIRROR_ALIASES = {
    "official": "official",
    "ustc": "ustc",
    "tuna": "tuna",
    "tsinghua": "tuna",
    "thu": "tuna",
}


def _normalize_apt_mirror_field(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = _APT_MIRROR_ALIASES.get(value.strip().lower())
    if normalized is None:
        raise ValueError("apt_mirror must be official, ustc, or tuna/tsinghua")
    return normalized


class ServerUpdate(SQLModel):
    """Schema for updating a server (password authentication only)"""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    host: Optional[str] = Field(None, min_length=1, max_length=255)
    ssh_port: Optional[int] = Field(None, ge=1, le=65535)
    ssh_user: Optional[str] = Field(None, min_length=1, max_length=100)
    ssh_password: Optional[str] = None
    sudo_password: Optional[str] = None
    apt_mirror: Optional[str] = Field(
        None,
        max_length=32,
        description="Preferred apt mirror: official, ustc, or tuna/tsinghua",
    )
    game_port: Optional[int] = Field(None, ge=1, le=65535)
    game_directory: Optional[str] = None
    description: Optional[str] = None

    # LGSM-style server configuration
    server_name: Optional[str] = Field(None, max_length=255)
    server_password: Optional[str] = None
    rcon_password: Optional[str] = None
    steam_account_token: Optional[str] = Field(
        None, max_length=255, description="Steam game server login token (GSLT)"
    )
    default_map: Optional[str] = Field(None, max_length=100)
    max_players: Optional[int] = Field(None, ge=1, le=64)
    game_mode: Optional[str] = Field(None, max_length=50)
    game_type: Optional[str] = Field(None, max_length=50)

    # Advanced parameters
    additional_parameters: Optional[str] = None
    ip_address: Optional[str] = None
    client_port: Optional[int] = Field(None, ge=1, le=65535)
    tv_port: Optional[int] = Field(None, ge=1, le=65535)
    tv_enable: Optional[bool] = None

    # Server-to-backend communication
    backend_url: Optional[str] = Field(
        None, max_length=500, description="Backend URL for status reporting (optional)"
    )

    # Auto-cleanup configuration
    auto_clear_crash_hours: Optional[int] = Field(
        None,
        ge=0,
        description="Hours offline before auto-clearing crash history (0 or None = disabled)",
    )

    # Web-based monitoring configuration
    enable_panel_monitoring: Optional[bool] = Field(
        None, description="Enable web panel monitoring and auto-restart"
    )
    monitor_interval_seconds: Optional[int] = Field(
        None, ge=10, le=3600, description="How often to check server status in seconds"
    )
    auto_restart_on_crash: Optional[bool] = Field(
        None, description="Auto-restart if process not found"
    )

    # A2S query configuration
    a2s_query_host: Optional[str] = Field(
        None, max_length=255, description="A2S query host (defaults to server host if not set)"
    )
    a2s_query_port: Optional[int] = Field(
        None, ge=1, le=65535, description="A2S query port (defaults to game port if not set)"
    )
    enable_a2s_monitoring: Optional[bool] = Field(None, description="Enable A2S query monitoring")
    a2s_failure_threshold: Optional[int] = Field(
        None, ge=1, le=10, description="Number of consecutive A2S failures before restart"
    )
    a2s_check_interval_seconds: Optional[int] = Field(
        None, ge=15, le=3600, description="A2S check interval in seconds (15-3600)"
    )

    # Auto-update configuration
    current_game_version: Optional[str] = Field(
        None, max_length=50, description="Current installed CS2 version"
    )
    enable_auto_update: Optional[bool] = Field(
        None, description="Enable automatic updates based on Steam API version check"
    )
    update_check_interval_hours: Optional[float] = Field(
        None,
        ge=0.0167,
        le=24.0,
        description="Hours between version checks (0.0167-24, where 0.0167≈1 minute)",
    )
    enable_plugin_auto_update: Optional[bool] = None
    plugin_update_check_interval_hours: Optional[float] = Field(None, ge=0.0167, le=24.0)

    # CPU affinity configuration
    cpu_affinity: Optional[str] = Field(
        None,
        max_length=500,
        description="Comma-separated list of CPU cores (e.g., '0,1,2,3' or '0-3,8-11')",
    )

    # Detached console session manager
    # A default of None makes the PATCH-style field omittable, while the
    # non-optional annotation rejects an explicitly supplied JSON null.
    session_manager: Optional[Literal["screen", "tmux"]] = Field(
        default=None,
        description="Terminal multiplexer used to run and control the CS2 process",
    )

    @field_validator("session_manager", mode="before")
    @classmethod
    def reject_explicit_null_session_manager(cls, value):
        if value is None:
            raise ValueError("session_manager cannot be null when supplied")
        return value

    # GitHub proxy configuration
    github_proxy: Optional[str] = Field(
        None,
        max_length=500,
        description="GitHub proxy URL (e.g., https://ghfast.top/https://github.com)",
    )

    # Panel proxy mode (mutually exclusive with github_proxy)
    use_panel_proxy: Optional[bool] = Field(
        None,
        description="Use panel server as proxy for all downloads (SteamCMD, GitHub). Mutually exclusive with github_proxy.",
    )
    clear_execstack_override: Optional[bool] = Field(default=None)
    execstack_fix_on_restart: Optional[bool] = Field(default=None)
    execstack_fix_on_framework: Optional[bool] = Field(default=None)
    execstack_fix_on_game_update: Optional[bool] = Field(default=None)
    execstack_fix_targets: Optional[List[str]] = Field(default=None)

    @field_validator("execstack_fix_targets")
    @classmethod
    def validate_execstack_fix_targets(cls, values):
        return list(normalize_execstack_targets(values)) if values is not None else None

    @field_validator("cpu_affinity")
    @classmethod
    def validate_cpu_affinity(cls, v):
        """Validate CPU affinity format to prevent command injection"""
        if v is None or v.strip() == "":
            return v
        # Only allow digits, commas, and hyphens
        if not re.match(r"^[\d,\-\s]+$", v):
            raise ValueError("CPU affinity must only contain digits, commas, and hyphens")
        return v.strip()

    @field_validator("default_map")
    @classmethod
    def validate_default_map(cls, v):
        return normalize_default_map(v) if v is not None else None

    @field_validator("game_mode")
    @classmethod
    def validate_game_mode(cls, v):
        return normalize_game_mode(v) if v is not None else None

    @field_validator("game_type")
    @classmethod
    def validate_game_type(cls, v):
        return normalize_game_type(v) if v is not None else None

    @field_validator("additional_parameters")
    @classmethod
    def validate_additional_parameters(cls, v):
        return normalize_additional_parameters(v)

    @field_validator("steam_account_token")
    @classmethod
    def validate_steam_account_token(cls, v):
        """Validate Steam account token format to prevent command injection"""
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        # Steam GSLT tokens are alphanumeric with no special characters that could cause shell injection
        if not re.match(r"^[A-Za-z0-9]+$", v):
            raise ValueError("Steam account token must only contain alphanumeric characters")
        return v

    @field_validator("apt_mirror")
    @classmethod
    def validate_apt_mirror(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return _normalize_apt_mirror_field(v)

    @model_validator(mode="after")
    def validate_proxy_mutual_exclusivity(self):
        """Ensure github_proxy and use_panel_proxy are mutually exclusive"""
        if self.github_proxy and self.use_panel_proxy:
            raise ValueError(
                "github_proxy and use_panel_proxy are mutually exclusive. Please choose only one."
            )
        return self
