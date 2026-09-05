"""Server request and response contracts for the versioned API."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from api.contracts.base import ApiRequest
from api.contracts.v1.identity import V1Model
from modules.models.servers import ServerStatus
from modules.server_startup import normalize_additional_parameters
from services.apt_mirrors import normalize_apt_mirror
from services.server_compatibility import DEFAULT_EXECSTACK_TARGETS, normalize_execstack_targets


class ServerSummary(V1Model):
    """Non-secret server projection for list and card views."""

    id: int
    name: str
    host: str
    game_port: int
    ssh_user: str
    status: ServerStatus
    description: str | None = None
    default_map: str
    max_players: int
    owner_id: int | None = None
    owner_username: str | None = None
    owner_is_admin: bool | None = None
    use_panel_proxy: bool = False
    github_proxy: str | None = None
    is_ssh_down: bool = False
    ssh_health_status: str = "unknown"
    consecutive_ssh_failures: int = 0
    ssh_health_failure_threshold: int = 84
    ssh_health_check_interval_hours: int = 2
    last_ssh_health_check: datetime | None = None
    os_id: str | None = None
    os_version: str | None = None
    clear_execstack_override: bool | None = None
    clear_execstack_effective: bool = False


class ServerDetail(ServerSummary):
    """Extended, still non-secret, server projection for the workspace."""

    ssh_port: int
    ssh_user: str
    game_directory: str
    game_mode: str
    game_type: str
    server_name: str
    session_manager: Literal["screen", "tmux"] = "tmux"
    enable_panel_monitoring: bool = False
    monitor_interval_seconds: int = 60
    auto_restart_on_crash: bool = True
    enable_a2s_monitoring: bool = False
    a2s_failure_threshold: int = 3
    a2s_check_interval_seconds: int = 60
    a2s_query_host: str | None = None
    a2s_query_port: int | None = None
    enable_auto_update: bool = True
    tv_enable: bool = False
    is_ssh_down: bool = False
    last_ssh_success: datetime | None = None
    created_at: datetime
    updated_at: datetime
    last_deployed: datetime | None = None
    apt_mirror: str | None = None
    additional_parameters: str | None = None
    has_sudo_password: bool = False
    ssh_pooled: bool = False
    ssh_in_use: bool = False
    ssh_active_leases: int = 0
    ssh_idle_seconds: float | None = None
    execstack_fix_on_restart: bool = True
    execstack_fix_on_framework: bool = True
    execstack_fix_on_game_update: bool = True
    execstack_fix_targets: list[str] = Field(
        default_factory=lambda: list(DEFAULT_EXECSTACK_TARGETS)
    )


class ServerWriteResult(ServerDetail):
    """Detail plus whether a running server needs a restart after the write."""

    restart_required: bool = False


class ServerCreateResult(ServerDetail):
    """Create response: the server plus host-initialization outcome."""

    host_initialized: bool = True
    missing_packages: list[str] = Field(default_factory=list)
    manual_install_command: str | None = None
    initialization_message: str = ""


class ServerCloneTemplate(V1Model):
    """Safe defaults used by the clone-server form."""

    source_server_id: int
    source_name: str
    host: str
    ssh_port: int
    ssh_user: str
    source_game_port: int
    source_game_directory: str
    has_sudo_password: bool = False
    apt_mirror: str | None = None
    use_panel_proxy: bool = False
    github_proxy: str | None = None
    name: str
    game_port: int
    game_directory: str
    server_name: str
    default_map: str
    max_players: int
    game_mode: str
    game_type: str
    session_manager: Literal["screen", "tmux"]
    additional_parameters: str | None = None


class ServerCloneRequest(ApiRequest):
    """Editable fields for cloning a server; credentials stay server-side."""

    name: str = Field(min_length=1, max_length=255)
    game_port: int = Field(ge=1, le=65534)
    game_directory: str = Field(min_length=1, max_length=500)
    description: str | None = None
    server_name: str = Field(min_length=1, max_length=255)
    default_map: str = Field(default="de_dust2", max_length=100)
    max_players: int = Field(default=32, ge=1, le=64)
    game_mode: str = Field(default="competitive", max_length=50)
    game_type: str = Field(default="0", max_length=50)
    session_manager: Literal["screen", "tmux"] | None = None
    apt_mirror: str | None = Field(default=None, max_length=32)
    sudo_password: str | None = Field(default=None, max_length=255)
    rcon_password: str | None = Field(default=None, max_length=255)
    steam_account_token: str | None = Field(default=None, max_length=255)
    additional_parameters: str | None = Field(default=None, max_length=4096)
    captcha_token: str | None = Field(default=None, min_length=1)
    captcha_code: str | None = Field(default=None, min_length=4, max_length=4)

    @field_validator("description", "sudo_password", "rcon_password")
    @classmethod
    def empty_optional_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("steam_account_token")
    @classmethod
    def validate_steam_account_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        token = value.strip()
        if not token:
            return None
        if not re.match(r"^[A-Za-z0-9]+$", token):
            raise ValueError("Steam account token must only contain alphanumeric characters")
        return token

    @field_validator("additional_parameters")
    @classmethod
    def validate_additional_parameters(cls, value: str | None) -> str | None:
        return normalize_additional_parameters(value)

    @field_validator("apt_mirror")
    @classmethod
    def validate_apt_mirror(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        normalized = normalize_apt_mirror(stripped)
        if normalized is None:
            raise ValueError("apt_mirror must be official, ustc, or tuna/tsinghua")
        return normalized

    @field_validator("default_map")
    @classmethod
    def validate_default_map(cls, value: str) -> str:
        return value.strip()

    @field_validator("game_mode", "game_type")
    @classmethod
    def strip_game_values(cls, value: str) -> str:
        return value.strip()


class ServerUpdateRequest(ApiRequest):
    """Partial server update. Secrets are write-only; omit to leave unchanged."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    ssh_user: str | None = Field(default=None, min_length=1, max_length=100)
    ssh_password: str | None = Field(default=None, max_length=255)
    game_port: int | None = Field(default=None, ge=1, le=65535)
    game_directory: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    server_name: str | None = Field(default=None, max_length=255)
    default_map: str | None = Field(default=None, max_length=100)
    max_players: int | None = Field(default=None, ge=1, le=64)
    game_mode: str | None = Field(default=None, max_length=50)
    game_type: str | None = Field(default=None, max_length=50)
    session_manager: Literal["screen", "tmux"] | None = None
    enable_panel_monitoring: bool | None = None
    monitor_interval_seconds: int | None = Field(default=None, ge=10, le=3600)
    auto_restart_on_crash: bool | None = None
    enable_a2s_monitoring: bool | None = None
    a2s_failure_threshold: int | None = Field(default=None, ge=1, le=10)
    a2s_check_interval_seconds: int | None = Field(default=None, ge=15, le=3600)
    a2s_query_host: str | None = Field(default=None, max_length=255)
    a2s_query_port: int | None = Field(default=None, ge=1, le=65535)
    enable_auto_update: bool | None = None
    tv_enable: bool | None = None
    rcon_password: str | None = Field(default=None, max_length=255)
    steam_account_token: str | None = Field(default=None, max_length=255)
    sudo_password: str | None = Field(default=None, max_length=255)
    apt_mirror: str | None = Field(default=None, max_length=32)
    use_panel_proxy: bool | None = None
    github_proxy: str | None = Field(default=None, max_length=500)
    additional_parameters: str | None = Field(default=None, max_length=4096)
    clear_execstack_override: bool | None = Field(default=None)
    execstack_fix_on_restart: bool | None = Field(default=None)
    execstack_fix_on_framework: bool | None = Field(default=None)
    execstack_fix_on_game_update: bool | None = Field(default=None)
    execstack_fix_targets: list[str] | None = Field(default=None)

    @field_validator("execstack_fix_targets")
    @classmethod
    def validate_execstack_targets(cls, value: list[str] | None) -> list[str] | None:
        return list(normalize_execstack_targets(value)) if value is not None else None

    @field_validator("ssh_password", "rcon_password", "description", "sudo_password")
    @classmethod
    def empty_optional_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("steam_account_token")
    @classmethod
    def validate_steam_account_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        token = value.strip()
        if not token:
            return None
        if not re.match(r"^[A-Za-z0-9]+$", token):
            raise ValueError("Steam account token must only contain alphanumeric characters")
        return token

    @field_validator("additional_parameters")
    @classmethod
    def validate_additional_parameters(cls, value: str | None) -> str | None:
        return normalize_additional_parameters(value)

    @field_validator("apt_mirror")
    @classmethod
    def validate_apt_mirror(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        normalized = normalize_apt_mirror(stripped)
        if normalized is None:
            raise ValueError("apt_mirror must be official, ustc, or tuna/tsinghua")
        return normalized

    @field_validator("github_proxy", "a2s_query_host")
    @classmethod
    def empty_github_proxy_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def validate_proxy_mutual_exclusivity(self) -> ServerUpdateRequest:
        if self.use_panel_proxy and self.github_proxy:
            raise ValueError(
                "github_proxy and use_panel_proxy are mutually exclusive. Please choose only one."
            )
        return self


class ServerCreateRequest(ApiRequest):
    """Create a server. Secret fields are write-only and never echoed."""

    name: str = Field(min_length=1, max_length=255)
    host: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(min_length=1, max_length=100)
    ssh_password: str = Field(min_length=1, max_length=255)
    sudo_password: str | None = Field(default=None, max_length=255)
    apt_mirror: str | None = Field(default=None, max_length=32)
    game_port: int = Field(default=27015, ge=1, le=65535)
    game_directory: str = Field(default="/home/cs2server/cs2", min_length=1, max_length=500)
    description: str | None = None
    captcha_token: str | None = Field(default=None, min_length=1)
    captcha_code: str | None = Field(default=None, min_length=4, max_length=4)
    force_add: bool = Field(
        default=False,
        description=(
            "Save the panel record without validating or initializing the host. "
            "Use only after explicit operator confirmation."
        ),
    )
    server_name: str = Field(default="CS2 Server", max_length=255)
    default_map: str = Field(default="de_dust2", max_length=100)
    max_players: int = Field(default=32, ge=1, le=64)
    game_mode: str = Field(default="competitive", max_length=50)
    game_type: str = Field(default="0", max_length=50)
    rcon_password: str | None = Field(default=None, max_length=255)
    steam_account_token: str | None = Field(default=None, max_length=255)
    additional_parameters: str | None = Field(default=None, max_length=4096)
    session_manager: Literal["screen", "tmux"] = "tmux"

    @field_validator("sudo_password", "description", "rcon_password")
    @classmethod
    def empty_secret_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("steam_account_token")
    @classmethod
    def validate_steam_account_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        token = value.strip()
        if not token:
            return None
        if not re.match(r"^[A-Za-z0-9]+$", token):
            raise ValueError("Steam account token must only contain alphanumeric characters")
        return token

    @field_validator("additional_parameters")
    @classmethod
    def validate_additional_parameters(cls, value: str | None) -> str | None:
        return normalize_additional_parameters(value)

    @field_validator("apt_mirror")
    @classmethod
    def validate_apt_mirror(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        normalized = normalize_apt_mirror(stripped)
        if normalized is None:
            raise ValueError("apt_mirror must be official, ustc, or tuna/tsinghua")
        return normalized


class ServerConfigImportRequest(ApiRequest):
    """Strict HTTP envelope for importing a portable server configuration."""

    format: Literal["upkk-cs2-server-config"] = "upkk-cs2-server-config"
    version: int = Field(default=1, ge=1, le=1)
    exported_at: datetime | None = None
    include_secrets: bool = False
    servers: list[dict[str, object]] = Field(min_length=1, max_length=100)
    conflict_strategy: Literal["skip", "update", "rename"] = "skip"


__all__ = [
    "ServerSummary",
    "ServerDetail",
    "ServerWriteResult",
    "ServerCreateResult",
    "ServerCloneTemplate",
    "ServerCloneRequest",
    "ServerUpdateRequest",
    "ServerCreateRequest",
    "ServerConfigImportRequest",
]
