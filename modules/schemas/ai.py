"""AI assistant request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import field_validator, model_validator
from sqlmodel import Field, SQLModel

ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
Verbosity = Literal["low", "medium", "high"]
TokenLimitParameter = Literal["max_completion_tokens", "max_tokens", "omit"]
AIAPIProtocol = Literal["chat_completions", "responses"]
# Keep the 256K default while allowing operators to match providers deployed
# with smaller KV-cache limits. Values are audited presets, not arbitrary input.
AIContextWindowTokens = Literal[
    8_192,
    16_384,
    32_768,
    65_536,
    131_072,
    262_144,
    393_216,
    1_048_576,
]


class AIModelParameters(SQLModel):
    reasoning_effort: Optional[ReasoningEffort] = None
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    top_p: Optional[float] = Field(default=None, ge=0, le=1)
    max_completion_tokens: Optional[int] = Field(default=None, ge=256, le=32768)
    token_limit_parameter: Optional[TokenLimitParameter] = None
    frequency_penalty: Optional[float] = Field(default=None, ge=-2, le=2)
    presence_penalty: Optional[float] = Field(default=None, ge=-2, le=2)
    verbosity: Optional[Verbosity] = None
    parallel_tool_calls: Optional[bool] = None

    @model_validator(mode="after")
    def use_one_sampling_control(self) -> "AIModelParameters":
        if self.temperature is not None and self.top_p is not None:
            raise ValueError("Set temperature or top_p, not both")
        return self


class AISystemSettingsResponse(SQLModel):
    enabled: bool
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_protocol: AIAPIProtocol
    api_key_configured: bool
    admin_prompt: Optional[str] = None
    private_endpoint_allowlist: list[str] = Field(default_factory=list)
    reasoning_effort: Optional[ReasoningEffort] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_completion_tokens: int
    token_limit_parameter: TokenLimitParameter
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    verbosity: Optional[Verbosity] = None
    parallel_tool_calls: Optional[bool] = None
    context_window_tokens: AIContextWindowTokens = 262_144
    requests_per_minute: int = Field(default=60, ge=1, le=10000)
    request_timeout_seconds: int
    history_retention_days: int
    max_provider_rounds: int
    max_tool_calls_per_round: int
    provider_tested: bool
    tool_calling_tested: bool
    streaming_tested: bool


class AISystemSettingsUpdate(AIModelParameters):
    enabled: Optional[bool] = None
    base_url: Optional[str] = Field(default=None, max_length=500)
    model: Optional[str] = Field(default=None, max_length=255)
    api_protocol: Optional[AIAPIProtocol] = None
    api_key: Optional[str] = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    admin_prompt: Optional[str] = Field(default=None, max_length=8000)
    private_endpoint_allowlist: Optional[list[str]] = None
    request_timeout_seconds: Optional[int] = Field(default=None, ge=5, le=120)
    history_retention_days: Optional[int] = Field(default=None, ge=1, le=7)
    max_provider_rounds: Optional[int] = Field(default=None, ge=1, le=1000)
    max_tool_calls_per_round: Optional[int] = Field(default=None, ge=1, le=1000)
    context_window_tokens: Optional[AIContextWindowTokens] = None
    requests_per_minute: Optional[int] = Field(default=None, ge=1, le=10000)


class UserAISettingsResponse(SQLModel):
    mode: Literal["global", "custom"]
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_protocol: AIAPIProtocol
    api_key_configured: bool
    reasoning_effort: Optional[ReasoningEffort] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_completion_tokens: int
    token_limit_parameter: TokenLimitParameter
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    verbosity: Optional[Verbosity] = None
    parallel_tool_calls: Optional[bool] = None
    provider_tested: bool
    tool_calling_tested: bool
    streaming_tested: bool
    effective_enabled: bool
    effective_source: Literal["global", "custom", "none"]


class UserAISettingsUpdate(AIModelParameters):
    mode: Literal["global", "custom"] = "global"
    base_url: Optional[str] = Field(default=None, max_length=500)
    model: Optional[str] = Field(default=None, max_length=255)
    api_protocol: Optional[AIAPIProtocol] = None
    api_key: Optional[str] = Field(default=None, max_length=4096)
    clear_api_key: bool = False


class AIProviderTestRequest(AIModelParameters):
    base_url: Optional[str] = Field(default=None, max_length=500)
    model: Optional[str] = Field(default=None, max_length=255)
    api_protocol: Optional[AIAPIProtocol] = None
    api_key: Optional[str] = Field(default=None, max_length=4096)


class AIProviderTestResponse(SQLModel):
    success: bool
    text_response_ok: bool
    tool_calling_ok: bool
    streaming_ok: bool
    message: str


class AIConversationCreate(SQLModel):
    server_id: Optional[int] = None
    title: Optional[str] = Field(default=None, max_length=255)


class AIConversationResponse(SQLModel):
    id: str
    server_id: Optional[int] = None
    title: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AIMessageResponse(SQLModel):
    id: int
    role: str
    content: Optional[str] = None
    tool_name: Optional[str] = None
    visible: bool
    created_at: Optional[datetime] = None


class AIConversationDetail(AIConversationResponse):
    messages: list[AIMessageResponse] = Field(default_factory=list)


class AIMessageCreate(SQLModel):
    content: str = Field(min_length=1, max_length=16000)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message cannot be empty")
        return value


class AIRunResponse(SQLModel):
    id: str
    conversation_id: str
    status: str
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class AIToolDecisionRequest(SQLModel):
    decision: Literal["approve", "reject"]
    arguments_hash: str = Field(min_length=64, max_length=64)


class AIToolRunResponse(SQLModel):
    id: str
    tool_name: str
    arguments: dict[str, Any]
    arguments_hash: str
    risk: str
    status: str
    requires_approval: bool
    plan_snapshot: Optional[dict[str, Any]] = None
    progress_snapshot: Optional[dict[str, Any]] = None
    progress_updated_at: Optional[datetime] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    approval_expires_at: Optional[datetime] = None


class AIBackgroundTaskToolResponse(SQLModel):
    id: str
    tool_name: str
    risk: str
    status: str
    plan_snapshot: Optional[dict[str, Any]] = None
    progress_snapshot: Optional[dict[str, Any]] = None
    progress_updated_at: Optional[datetime] = None
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class AIBackgroundTaskResponse(SQLModel):
    id: str
    conversation_id: str
    server_id: Optional[int] = None
    status: str
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    tools: list[AIBackgroundTaskToolResponse] = Field(default_factory=list)
