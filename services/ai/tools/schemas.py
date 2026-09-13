"""Pydantic input models for AI assistant tools."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyInput(ToolInput):
    pass


class KnowledgeInput(ToolInput):
    topic: Literal[
        "layout",
        "deployment",
        "steamcmd",
        "startup",
        "logs_and_config",
        "metamod",
        "counterstrikesharp",
        "plugins",
        "workshop_maps",
    ]


class FileSearchInput(ToolInput):
    query: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(default=".", max_length=500)
    search_content: bool = False
    limit: int = Field(default=50, ge=1, le=100)


class FileReadInput(ToolInput):
    relative_path: str = Field(min_length=1, max_length=500)


class TailLogInput(ToolInput):
    lines: int = Field(default=120, ge=10, le=500)


class GameConsoleReadInput(ToolInput):
    lines: int = Field(
        default=120,
        ge=10,
        le=500,
        description="Number of recent lines to read from the live screen/tmux game console.",
    )


class CSSLogListInput(ToolInput):
    keyword: str | None = Field(default=None, min_length=1, max_length=64)
    limit: int = Field(default=20, ge=1, le=50)


class CSSLogReadInput(ToolInput):
    log_name: str = Field(min_length=1, max_length=255)
    keyword: str | None = Field(default=None, min_length=1, max_length=64)
    lines: int = Field(default=400, ge=20, le=2000)


class DiagnosticPlanInput(ToolInput):
    scope: Literal["metamod", "counterstrikesharp", "both"] = "both"


class DiagnosticExecuteInput(DiagnosticPlanInput):
    expected_plan_hash: str = Field(min_length=64, max_length=64)


class DiagnosticRunInput(ToolInput):
    diagnostic_id: str = Field(min_length=36, max_length=36)


class GitHubSearchInput(ToolInput):
    query: str = Field(min_length=1, max_length=120)


class GitHubInspectInput(ToolInput):
    repo_url: str = Field(min_length=1, max_length=500)
    mode: Literal["install", "upgrade"] = "install"


class GitHubPlanInput(GitHubInspectInput):
    asset_name: str | None = Field(default=None, max_length=500)
    config_policy: Literal["preserve", "overwrite"] = "preserve"
    recipe_id: int | None = Field(default=None, gt=0)


class GitHubApplyInput(GitHubPlanInput):
    expected_plan_hash: str = Field(min_length=64, max_length=64)
    acknowledge_warning_rule_ids: list[int] = Field(default_factory=list)
    acknowledge_unknown_compatibility: bool = False


class PluginSearchInput(ToolInput):
    query: str = Field(default="", max_length=128)
    category: str | None = Field(default=None, max_length=40)
    limit: int = Field(default=10, ge=1, le=20)


class PluginPlanInput(ToolInput):
    plugin_id: int = Field(gt=0)


class WorkshopPlanInput(ToolInput):
    workshop_id_or_url: str = Field(min_length=1, max_length=512)
    name: str | None = Field(default=None, max_length=128)
    enabled: bool = True
    min_players: int = Field(default=0, ge=0, le=64)
    only_nominate: bool = False
    restricted_times: str = Field(default="", max_length=512)


class ServerOperationInput(ToolInput):
    operation: Literal[
        "deploy",
        "update",
        "validate",
        "install_metamod",
        "install_counterstrikesharp",
    ]


class ServerControlInput(ToolInput):
    action: Literal["start", "stop", "restart"]


class GameConsoleCommandInput(ToolInput):
    command: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "One literal CS2 console command. This is sent to the detached game process and "
            "is never executed as host Shell."
        ),
    )

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Game console command cannot be blank")
        if "\x00" in value or "\r" in value or "\n" in value:
            raise ValueError("Only one game console command is allowed per confirmation")
        return value


class MapPoolSearchInput(ToolInput):
    query: str = Field(
        min_length=1,
        max_length=128,
        description="Map name fragment or Workshop ID from the server MapChooser pool.",
    )


class ChangeCurrentMapInput(ToolInput):
    query: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "Map name fragment or Workshop ID. The query must uniquely match one pool entry "
            "or be a valid Workshop ID. Workshop maps send host_workshop_map {id}."
        ),
    )


class ServerStartupPlanInput(ToolInput):
    default_map: str | None = Field(
        default=None,
        max_length=100,
        description="Default map name or Workshop map path; omit to keep the current value",
    )
    max_players: int | None = Field(
        default=None,
        ge=1,
        le=64,
        description="Maximum player slots; omit to keep the current value",
    )
    game_mode: str | None = Field(
        default=None,
        max_length=50,
        description=(
            "Named CS2 mode (casual, competitive, wingman, arms_race, demolition, "
            "deathmatch, custom) or numeric game_mode; omit to keep the current value"
        ),
    )
    game_type: str | None = Field(
        default=None,
        max_length=1,
        description=(
            "Numeric game_type from 0 to 9. Usually omit this because named game modes "
            "synchronize it automatically"
        ),
    )
    additional_parameters: str | None = Field(
        default=None,
        max_length=4096,
        description=(
            "Additional CS2 +parameter/-parameter arguments. Empty string clears existing "
            "arguments. Shell commands and dedicated panel settings are rejected"
        ),
    )

    @model_validator(mode="after")
    def require_startup_change(self):
        editable = {
            "default_map",
            "max_players",
            "game_mode",
            "game_type",
            "additional_parameters",
        }
        if not (self.model_fields_set & editable):
            raise ValueError("At least one startup setting must be supplied")
        return self


class ApplyServerStartupPlanInput(ServerStartupPlanInput):
    expected_plan_hash: str = Field(min_length=64, max_length=64)


class FilePatchInput(ToolInput):
    relative_path: str = Field(min_length=1, max_length=500)
    expected_revision: str = Field(
        min_length=64,
        max_length=64,
        description=(
            "Exact lowercase SHA-256 revision returned by read_server_text_file; "
            "file creation is not supported"
        ),
    )
    content: str = Field(max_length=256_000)

    @field_validator("expected_revision", mode="before")
    @classmethod
    def validate_expected_revision(cls, value: Any) -> Any:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError(
                "expected_revision must be the exact SHA-256 revision returned by "
                "read_server_text_file; restart first if the plugin has not generated its "
                "configuration file, and never use 'new'"
            )
        return value


class ApplyPluginPlanInput(PluginPlanInput):
    expected_plan_hash: str = Field(min_length=64, max_length=64)
    acknowledge_warning_rule_ids: list[int] = Field(default_factory=list)


class ApplyWorkshopPlanInput(WorkshopPlanInput):
    expected_plan_hash: str = Field(min_length=64, max_length=64)
    acknowledge_warning_rule_ids: list[int] = Field(default_factory=list)


class SavedHostCommandInput(ToolInput):
    command_id: int = Field(gt=0)
    expected_command_hash: str = Field(min_length=64, max_length=64)


class ManagedPluginUpgradeInput(ToolInput):
    plugin_id: int = Field(gt=0)


class ApplyManagedPluginUpgradeInput(ManagedPluginUpgradeInput):
    expected_plan_hash: str = Field(min_length=64, max_length=64)
