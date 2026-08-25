"""Strict Discord Bot and server AI policy API contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, ConfigDict, field_validator, model_validator
from sqlmodel import Field, SQLModel


class DiscordCapability(StrEnum):
    STATUS = "status"
    START = "start"
    STOP = "stop"
    RESTART = "restart"
    UPDATE = "update"
    VALIDATE = "validate"
    PLUGIN_BROWSE = "plugin_browse"
    PLUGIN_INSTALL = "plugin_install"
    PLUGIN_UPGRADE = "plugin_upgrade"
    AGENT_ASK = "agent_ask"


class AgentCapability(StrEnum):
    INSPECT_STATUS = "inspect_status"
    READ_LOGS_FILES = "read_logs_files"
    BROWSE_PLAN_PLUGINS = "browse_plan_plugins"
    START = "start"
    STOP = "stop"
    RESTART = "restart"
    DEPLOY = "deploy"
    UPDATE = "update"
    VALIDATE = "validate"
    MANAGE_FRAMEWORKS = "manage_frameworks"
    INSTALL_MARKET_PLUGINS = "install_market_plugins"
    INSTALL_OR_UPGRADE_GITHUB_PLUGINS = "install_or_upgrade_github_plugins"
    UPGRADE_MANAGED_PLUGINS = "upgrade_managed_plugins"
    WRITE_CONFIGURATION = "write_configuration"
    MANAGE_WORKSHOP_MAPS = "manage_workshop_maps"
    RUN_PLUGIN_DIAGNOSTICS = "run_plugin_diagnostics"
    EXECUTE_SAVED_HOST_COMMANDS = "execute_saved_host_commands"


DEFAULT_AGENT_CAPABILITIES = [
    AgentCapability.INSPECT_STATUS,
    AgentCapability.READ_LOGS_FILES,
    AgentCapability.BROWSE_PLAN_PLUGINS,
]


def _validate_snowflake(value: str) -> str:
    value = value.strip()
    if not value.isdecimal() or value.startswith("0") or len(value) > 20:
        raise ValueError("Discord ID must be a positive decimal Snowflake string")
    if int(value) > 18_446_744_073_709_551_615:
        raise ValueError("Discord ID exceeds the unsigned 64-bit Snowflake range")
    return value


Snowflake = Annotated[str, AfterValidator(_validate_snowflake)]


class DiscordBotSettingsUpdate(SQLModel):
    token: str | None = Field(default=None, min_length=20, max_length=4096)
    enabled: bool | None = None

    @field_validator("token")
    @classmethod
    def normalize_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Bot Token cannot be blank")
        return value


class DiscordBotSettingsResponse(SQLModel):
    enabled: bool
    token_configured: bool
    application_id: Snowflake | None = None
    bot_user_id: Snowflake | None = None
    username: str | None = None
    discriminator: str | None = None
    connection_status: str
    last_connected_at: datetime | None = None
    last_error: str | None = None
    invite_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


class DiscordBotTestRequest(SQLModel):
    token: str | None = Field(default=None, min_length=20, max_length=4096)

    @field_validator("token")
    @classmethod
    def normalize_token(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class DiscordBotTestResponse(SQLModel):
    success: bool
    application_id: Snowflake | None = None
    bot_user_id: Snowflake | None = None
    username: str | None = None
    message: str


class DiscordGuildOption(SQLModel):
    id: Snowflake
    name: str
    icon: str | None = None


class DiscordChannelOption(SQLModel):
    id: Snowflake
    guild_id: Snowflake
    name: str
    type: int


class DiscordRoleOption(SQLModel):
    id: Snowflake
    guild_id: Snowflake
    name: str
    position: int = 0


class DiscordBotOptionsResponse(SQLModel):
    guilds: list[DiscordGuildOption] = Field(default_factory=list)
    channels: list[DiscordChannelOption] = Field(default_factory=list)
    roles: list[DiscordRoleOption] = Field(default_factory=list)


class DiscordBindingUpdate(SQLModel):
    enabled: bool = False
    guild_id: Snowflake | None = None
    channel_ids: list[Snowflake] = Field(default_factory=list)
    role_ids: list[Snowflake] = Field(default_factory=list)
    user_ids: list[Snowflake] = Field(default_factory=list)
    capabilities: list[DiscordCapability] = Field(default_factory=list)
    response_visibility: Literal["public"] = "public"

    @field_validator("channel_ids", "role_ids", "user_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    @field_validator("capabilities")
    @classmethod
    def unique_capabilities(cls, value: list[DiscordCapability]) -> list[DiscordCapability]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_enabled_binding(self) -> "DiscordBindingUpdate":
        if self.enabled:
            if self.guild_id is None:
                raise ValueError("An enabled Discord binding requires a Guild")
            if not self.channel_ids:
                raise ValueError("An enabled Discord binding requires at least one channel")
            if not self.role_ids and not self.user_ids:
                raise ValueError("An enabled Discord binding requires at least one role or user")
        return self


class DiscordBindingResponse(SQLModel):
    server_id: int
    enabled: bool
    effective_enabled: bool
    disabled_reason: str | None = None
    guild_id: Snowflake | None = None
    channel_ids: list[Snowflake] = Field(default_factory=list)
    role_ids: list[Snowflake] = Field(default_factory=list)
    user_ids: list[Snowflake] = Field(default_factory=list)
    capabilities: list[DiscordCapability] = Field(default_factory=list)
    response_visibility: Literal["public"] = "public"

    model_config = ConfigDict(from_attributes=True)


class AgentPolicyUpdate(SQLModel):
    enabled: bool = True
    capabilities: list[AgentCapability] = Field(
        default_factory=lambda: list(DEFAULT_AGENT_CAPABILITIES)
    )

    @field_validator("capabilities")
    @classmethod
    def unique_capabilities(cls, value: list[AgentCapability]) -> list[AgentCapability]:
        return list(dict.fromkeys(value))


class AgentPolicyResponse(SQLModel):
    server_id: int
    enabled: bool
    effective_enabled: bool
    disabled_reason: str | None = None
    capabilities: list[AgentCapability]

    model_config = ConfigDict(from_attributes=True)
