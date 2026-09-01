"""Game and plugin update contracts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from api.contracts.base import ApiRequest
from api.contracts.v1.identity import V1Model
from api.contracts.v1.plugins import ManagedPluginUpdateView


class PluginUpdatesView(V1Model):
    enable_plugin_auto_update: bool
    plugin_update_check_interval_hours: float
    last_plugin_update_check: datetime | None = None
    enable_plugin_post_update_commands: bool = False
    plugin_post_update_command_ids: list[int] = Field(default_factory=list)
    plugins: list[ManagedPluginUpdateView] = Field(default_factory=list)


class PluginUpdatesSettingsRequest(ApiRequest):
    enable_plugin_auto_update: bool
    plugin_update_check_interval_hours: float = Field(ge=0.0167, le=24.0)
    enable_plugin_post_update_commands: bool = False
    plugin_post_update_command_ids: list[int] = Field(default_factory=list)


class ManagedPluginRegisterRequest(ApiRequest):
    """Register an already-installed plugin or framework for auto-update."""

    source_type: Literal["github", "market", "framework"] = "github"
    source_key: str | None = Field(default=None, max_length=500)
    display_name: str = Field(min_length=1, max_length=255)
    repo_url: str | None = Field(default=None, max_length=500)
    market_plugin_id: int | None = None
    framework_key: str | None = Field(default=None, max_length=100)
    installed_release_id: str | None = Field(default=None, max_length=100)
    installed_version: str = Field(default="unknown", max_length=100)
    asset_glob: str | None = Field(default=None, max_length=500)
    custom_install_path: str | None = Field(default=None, max_length=255)
    exclude_dirs: list[str] = Field(default_factory=list)
    exclude_files: list[str] = Field(default_factory=list)
    auto_update_enabled: bool = False
    backup_before_update: bool = False
    restart_after_update: bool = False

    @field_validator("repo_url")
    @classmethod
    def validate_register_repo_url(cls, value: str | None) -> str | None:
        if not value:
            return value
        text = value.strip().rstrip("/")
        if not re.match(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?$", text):
            raise ValueError("repo_url must be a GitHub repository URL")
        return text

    @field_validator("exclude_dirs", "exclude_files")
    @classmethod
    def validate_register_exclusions(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            text = str(value).replace("\\", "/").strip()
            if not text:
                continue
            if ".." in text.split("/") or text.startswith("/") or "\x00" in text:
                raise ValueError("exclusion paths must be relative and cannot contain traversal")
            cleaned.append(text)
        return cleaned


class PluginUpdatesPluginPatch(ApiRequest):
    auto_update_enabled: bool | None = None
    backup_before_update: bool | None = None
    restart_after_update: bool | None = None
    exclude_dirs: list[str] | None = None
    exclude_files: list[str] | None = None

    @field_validator("exclude_dirs", "exclude_files")
    @classmethod
    def validate_update_exclusions(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        cleaned: list[str] = []
        for value in values:
            text = str(value).replace("\\", "/").strip()
            if not text:
                continue
            if ".." in text.split("/") or text.startswith("/") or "\x00" in text:
                raise ValueError("exclusion paths must be relative and cannot contain traversal")
            cleaned.append(text)
        return cleaned


class PluginUpdateStatusView(V1Model):
    state: str = "idle"
    phase: str = "idle"
    message: str | None = None
    current: int = 0
    total: int = 0
    logs: list[str] = Field(default_factory=list)
    started_at: datetime | str | None = None
    finished_at: datetime | str | None = None


class GameUpdatesView(V1Model):
    """Steam advertised version versus installed steam.inf, plus game auto-update."""

    installed_version: str | None = None
    installed_build_id: str | None = None
    installed_source: Literal["steam.inf", "database", "unknown"] = "unknown"
    advertised_version: str | None = None
    up_to_date: bool | None = None
    steam_check_ok: bool = False
    steam_message: str | None = None
    steam_error: str | None = None
    enable_auto_update: bool = True
    update_check_interval_hours: float = 1.0
    last_update_check: datetime | None = None
    last_update_time: datetime | None = None
    current_game_version: str | None = None

    @field_validator(
        "installed_version",
        "installed_build_id",
        "advertised_version",
        "current_game_version",
        mode="before",
    )
    @classmethod
    def stringify_optional_version_fields(cls, value: object) -> str | None:
        """Redis JSON turns numeric build ids into ints; keep the public contract as text."""
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class GameUpdatesSettingsRequest(ApiRequest):
    enable_auto_update: bool
    update_check_interval_hours: float = Field(ge=0.0167, le=24.0)


class GameUpdateOperationRequest(ApiRequest):
    action: Literal["update", "validate"]


class CustomCommandView(V1Model):
    """Saved host or game-process shortcut. Command text is user-authored, not a secret."""

    id: int
    server_id: int
    name: str
    target: Literal["host", "game_process"]
    commands: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CustomCommandWriteRequest(ApiRequest):
    name: str = Field(min_length=1, max_length=255)
    target: Literal["host", "game_process"] = "host"
    commands: str = Field(min_length=1, max_length=20000)


class CustomCommandExecuteBody(ApiRequest):
    target: Literal["host", "game_process"] = "host"
    commands: str = Field(min_length=1, max_length=20000)


class CustomCommandExecuteView(V1Model):
    success: bool
    message: str
    log: str = ""


class StartupCommandView(V1Model):
    """Masked startup-command preview. Passwords and tokens are never returned in clear text."""

    startup_command: str
    cs2_command: str
    session_manager: str
    game_mode_resolved: str


class ConfirmDeploymentView(V1Model):
    success: bool
    message: str
    status: str
    last_deployed: datetime | None = None


__all__ = [
    "PluginUpdatesView",
    "PluginUpdatesSettingsRequest",
    "ManagedPluginRegisterRequest",
    "PluginUpdatesPluginPatch",
    "PluginUpdateStatusView",
    "GameUpdatesView",
    "GameUpdatesSettingsRequest",
    "GameUpdateOperationRequest",
    "CustomCommandView",
    "CustomCommandWriteRequest",
    "CustomCommandExecuteBody",
    "CustomCommandExecuteView",
    "StartupCommandView",
    "ConfirmDeploymentView",
]
