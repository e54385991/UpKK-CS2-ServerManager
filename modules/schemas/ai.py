"""AI assistant request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import field_validator
from sqlmodel import Field, SQLModel


class AISystemSettingsResponse(SQLModel):
    enabled: bool
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key_configured: bool
    admin_prompt: Optional[str] = None
    private_endpoint_allowlist: list[str] = Field(default_factory=list)
    request_timeout_seconds: int
    history_retention_days: int
    provider_tested: bool
    tool_calling_tested: bool


class AISystemSettingsUpdate(SQLModel):
    enabled: Optional[bool] = None
    base_url: Optional[str] = Field(default=None, max_length=500)
    model: Optional[str] = Field(default=None, max_length=255)
    api_key: Optional[str] = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    admin_prompt: Optional[str] = Field(default=None, max_length=8000)
    private_endpoint_allowlist: Optional[list[str]] = None
    request_timeout_seconds: Optional[int] = Field(default=None, ge=5, le=120)
    history_retention_days: Optional[int] = Field(default=None, ge=1, le=365)


class UserAISettingsResponse(SQLModel):
    mode: Literal["global", "custom"]
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key_configured: bool
    provider_tested: bool
    tool_calling_tested: bool
    effective_enabled: bool
    effective_source: Literal["global", "custom", "none"]


class UserAISettingsUpdate(SQLModel):
    mode: Literal["global", "custom"] = "global"
    base_url: Optional[str] = Field(default=None, max_length=500)
    model: Optional[str] = Field(default=None, max_length=255)
    api_key: Optional[str] = Field(default=None, max_length=4096)
    clear_api_key: bool = False


class AIProviderTestRequest(SQLModel):
    base_url: Optional[str] = Field(default=None, max_length=500)
    model: Optional[str] = Field(default=None, max_length=255)
    api_key: Optional[str] = Field(default=None, max_length=4096)


class AIProviderTestResponse(SQLModel):
    success: bool
    text_response_ok: bool
    tool_calling_ok: bool
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

    model_config = {"from_attributes": True}


class AIMessageResponse(SQLModel):
    id: int
    role: str
    content: Optional[str] = None
    tool_name: Optional[str] = None
    visible: bool
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


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

    model_config = {"from_attributes": True}


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
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    approval_expires_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
