"""Assistant, Discord and scheduled-task contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from api.contracts.base import ApiRequest
from api.contracts.v1.identity import V1Model


class ConsoleWorkspaceView(V1Model):
    """Game and SSH console status. GET stays 200 when SSH is down."""

    server_id: int
    host: str
    session_manager: Literal["screen", "tmux"] = "tmux"
    ssh_ok: bool
    ssh_error: str | None = None
    game_running: bool = False
    steamcmd_running: bool = False
    message: str | None = None


class ConsolePaneView(V1Model):
    """Live tmux/screen pane snapshot. GET stays 200 when SSH or the session is down."""

    server_id: int
    kind: Literal["game", "steamcmd"]
    session_name: str
    session_manager: Literal["screen", "tmux"] | None = None
    ssh_ok: bool
    running: bool = False
    text: str = ""
    heartbeat: str | None = None
    message: str | None = None


class AssistantConversationView(V1Model):
    id: str
    server_id: int | None = None
    title: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AssistantMessageView(V1Model):
    id: int
    role: str
    content: str | None = None
    tool_name: str | None = None
    created_at: datetime | None = None


class AssistantConversationDetailView(AssistantConversationView):
    messages: list[AssistantMessageView] = Field(default_factory=list)


class AssistantWorkspaceView(V1Model):
    provider_ready: bool
    mode: Literal["global", "custom", "none"]
    model: str | None = None
    conversations: list[AssistantConversationView] = Field(default_factory=list)


class AssistantConversationCreateRequest(ApiRequest):
    title: str | None = Field(default=None, max_length=255)
    server_id: int | None = None


class AssistantMessageCreateRequest(ApiRequest):
    content: str = Field(min_length=1, max_length=16000)


class AssistantRunView(V1Model):
    id: str
    conversation_id: str
    status: str
    error: str | None = None


class AssistantToolView(V1Model):
    id: str
    tool_name: str
    arguments_hash: str
    risk: str
    status: str
    requires_approval: bool
    error: str | None = None


class AssistantRunDetailView(AssistantRunView):
    tools: list[AssistantToolView] = Field(default_factory=list)


class AssistantToolDecisionRequest(ApiRequest):
    decision: Literal["approve", "reject"]
    arguments_hash: str = Field(min_length=64, max_length=64)


class DiscordBotView(V1Model):
    enabled: bool
    token_configured: bool
    message_trigger_mode: Literal["mention_only", "mention_and_greetings"]
    username: str | None = None
    connection_status: str
    last_error: str | None = None
    invite_url: str | None = None


class DiscordBotUpdateRequest(ApiRequest):
    token: str | None = Field(default=None, min_length=20, max_length=4096)
    enabled: bool | None = None
    message_trigger_mode: Literal["mention_only", "mention_and_greetings"] | None = None


class DiscordBotTestBody(ApiRequest):
    token: str | None = Field(default=None, min_length=20, max_length=4096)


class DiscordBotTestView(V1Model):
    success: bool
    username: str | None = None
    message: str


class DiscordGuildView(V1Model):
    id: str
    name: str
    icon: str | None = None


class DiscordChannelView(V1Model):
    id: str
    guild_id: str
    name: str
    type: int = 0


class DiscordRoleView(V1Model):
    id: str
    guild_id: str
    name: str
    position: int = 0


class DiscordOptionsView(V1Model):
    """Guild/channel/role picker. GET stays 200 when no bot token is stored."""

    token_configured: bool
    guilds: list[DiscordGuildView] = Field(default_factory=list)
    channels: list[DiscordChannelView] = Field(default_factory=list)
    roles: list[DiscordRoleView] = Field(default_factory=list)
    message: str | None = None


class DiscordBindingView(V1Model):
    server_id: int
    enabled: bool
    effective_enabled: bool
    disabled_reason: str | None = None
    guild_id: str | None = None
    channel_ids: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    user_ids: list[str] = Field(default_factory=list)
    allow_channel_managers: bool = False
    allow_server_administrators: bool = False
    capabilities: list[str] = Field(default_factory=list)
    response_visibility: Literal["public"] = "public"


class DiscordBindingUpdateRequest(ApiRequest):
    enabled: bool = False
    guild_id: str | None = None
    channel_ids: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    user_ids: list[str] = Field(default_factory=list)
    allow_channel_managers: bool = False
    allow_server_administrators: bool = False
    capabilities: list[str] = Field(default_factory=list)
    sync_existing_servers: bool = False


class DiscordGlobalBindingView(V1Model):
    configured: bool
    enabled: bool
    guild_id: str | None = None
    channel_ids: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    user_ids: list[str] = Field(default_factory=list)
    allow_channel_managers: bool = False
    allow_server_administrators: bool = False
    capabilities: list[str] = Field(default_factory=list)
    server_count: int = 0
    matching_server_count: int = 0
    synced_server_count: int = 0
    inherited_by_new_servers: bool = True


class DiscordMenuPushBody(ApiRequest):
    guild_id: str
    channel_id: str


class DiscordMenuPushView(V1Model):
    guild_id: str
    channel_id: str
    message_id: str
    expires_in_seconds: int = 300


class AgentPolicyView(V1Model):
    server_id: int
    enabled: bool
    effective_enabled: bool
    disabled_reason: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class AgentPolicyUpdateRequest(ApiRequest):
    enabled: bool = True
    capabilities: list[str] = Field(default_factory=list)


class AssistantSystemSettingsView(V1Model):
    """Admin AI provider. API keys stay write-only."""

    enabled: bool
    base_url: str | None = None
    model: str | None = None
    api_protocol: Literal["chat_completions", "responses"]
    api_key_configured: bool
    admin_prompt: str | None = None
    private_endpoint_allowlist: list[str] = Field(default_factory=list)
    reasoning_effort: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_completion_tokens: int = 2048
    token_limit_parameter: Literal["max_completion_tokens", "max_tokens", "omit"] = (
        "max_completion_tokens"
    )
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    verbosity: str | None = None
    parallel_tool_calls: bool | None = None
    context_window_tokens: Literal[262144, 393216, 1048576] = 262144
    request_timeout_seconds: int
    history_retention_days: int
    max_provider_rounds: int
    max_tool_calls_per_round: int
    provider_tested: bool
    tool_calling_tested: bool
    streaming_tested: bool


class AssistantSystemSettingsPatch(ApiRequest):
    enabled: bool | None = None
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=255)
    api_protocol: Literal["chat_completions", "responses"] | None = None
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    admin_prompt: str | None = Field(default=None, max_length=8000)
    private_endpoint_allowlist: list[str] | None = None
    reasoning_effort: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_completion_tokens: int | None = Field(default=None, ge=256, le=32768)
    token_limit_parameter: Literal["max_completion_tokens", "max_tokens", "omit"] | None = None
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    verbosity: str | None = None
    parallel_tool_calls: bool | None = None
    request_timeout_seconds: int | None = Field(default=None, ge=5, le=120)
    history_retention_days: int | None = Field(default=None, ge=1, le=7)
    max_provider_rounds: int | None = Field(default=None, ge=1, le=1000)
    max_tool_calls_per_round: int | None = Field(default=None, ge=1, le=1000)
    context_window_tokens: Literal[262144, 393216, 1048576] | None = None


class AssistantProviderTestBody(ApiRequest):
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=255)
    api_protocol: Literal["chat_completions", "responses"] | None = None
    api_key: str | None = Field(default=None, max_length=4096)


class AssistantProviderTestView(V1Model):
    success: bool
    text_response_ok: bool
    tool_calling_ok: bool
    streaming_ok: bool
    message: str


class ScheduledTaskView(V1Model):
    id: int
    server_id: int
    name: str
    action: str
    enabled: bool
    schedule_type: str
    schedule_value: str
    last_run: datetime | None = None
    next_run: datetime | None = None
    run_count: int = 0
    last_status: str | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ScheduledTaskCreateRequest(ApiRequest):
    name: str = Field(min_length=1, max_length=255)
    action: Literal["start", "stop", "restart", "update", "validate", "backup_plugins"]
    enabled: bool = True
    schedule_type: Literal["daily", "weekly", "interval", "cron"]
    schedule_value: str = Field(min_length=1, max_length=255)


class ScheduledTaskUpdateRequest(ApiRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    action: Literal["start", "stop", "restart", "update", "validate", "backup_plugins"] | None = (
        None
    )
    enabled: bool | None = None
    schedule_type: Literal["daily", "weekly", "interval", "cron"] | None = None
    schedule_value: str | None = Field(default=None, min_length=1, max_length=255)


__all__ = [
    "ConsoleWorkspaceView",
    "ConsolePaneView",
    "AssistantConversationView",
    "AssistantMessageView",
    "AssistantConversationDetailView",
    "AssistantWorkspaceView",
    "AssistantConversationCreateRequest",
    "AssistantMessageCreateRequest",
    "AssistantRunView",
    "AssistantToolView",
    "AssistantRunDetailView",
    "AssistantToolDecisionRequest",
    "DiscordBotView",
    "DiscordBotUpdateRequest",
    "DiscordBotTestBody",
    "DiscordBotTestView",
    "DiscordGuildView",
    "DiscordChannelView",
    "DiscordRoleView",
    "DiscordOptionsView",
    "DiscordBindingView",
    "DiscordBindingUpdateRequest",
    "DiscordGlobalBindingView",
    "DiscordMenuPushBody",
    "DiscordMenuPushView",
    "AgentPolicyView",
    "AgentPolicyUpdateRequest",
    "AssistantSystemSettingsView",
    "AssistantSystemSettingsPatch",
    "AssistantProviderTestBody",
    "AssistantProviderTestView",
    "ScheduledTaskView",
    "ScheduledTaskCreateRequest",
    "ScheduledTaskUpdateRequest",
]
