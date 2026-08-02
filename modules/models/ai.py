"""Persistent AI assistant configuration, conversations, and audit state."""

# ruff: noqa: F403,F405

import uuid

from .common import *


def _uuid() -> str:
    return str(uuid.uuid4())


class AISystemSettings(SQLModel, table=True):
    """Singleton site-wide AI configuration."""

    __tablename__ = "ai_system_settings"

    id: Optional[int] = Field(default=1, primary_key=True)
    enabled: bool = Field(default=False)
    base_url: Optional[str] = Field(default=None, max_length=500)
    model: Optional[str] = Field(default=None, max_length=255)
    api_key_encrypted: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    admin_prompt: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    private_endpoint_allowlist: List[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    reasoning_effort: Optional[str] = Field(default=None, max_length=16)
    temperature: Optional[float] = Field(default=None)
    top_p: Optional[float] = Field(default=None)
    max_completion_tokens: int = Field(default=2048)
    token_limit_parameter: str = Field(default="max_completion_tokens", max_length=32)
    frequency_penalty: Optional[float] = Field(default=None)
    presence_penalty: Optional[float] = Field(default=None)
    verbosity: Optional[str] = Field(default=None, max_length=16)
    parallel_tool_calls: Optional[bool] = Field(default=None)
    request_timeout_seconds: int = Field(default=60)
    history_retention_days: int = Field(default=7)
    max_provider_rounds: int = Field(default=30)
    provider_tested: bool = Field(default=False)
    tool_calling_tested: bool = Field(default=False)
    streaming_tested: bool = Field(default=False)
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )

    @classmethod
    async def get_or_create(cls, session: AsyncSession) -> "AISystemSettings":
        settings = await session.get(cls, 1)
        if settings is None:
            settings = cls(id=1)
            session.add(settings)
            await session.commit()
            await session.refresh(settings)
        return settings


class UserAISettings(SQLModel, table=True):
    """Optional per-user OpenAI-compatible provider override."""

    __tablename__ = "user_ai_settings"

    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    )
    mode: str = Field(default="global", max_length=16)
    base_url: Optional[str] = Field(default=None, max_length=500)
    model: Optional[str] = Field(default=None, max_length=255)
    api_key_encrypted: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    reasoning_effort: Optional[str] = Field(default=None, max_length=16)
    temperature: Optional[float] = Field(default=None)
    top_p: Optional[float] = Field(default=None)
    max_completion_tokens: int = Field(default=2048)
    token_limit_parameter: str = Field(default="max_completion_tokens", max_length=32)
    frequency_penalty: Optional[float] = Field(default=None)
    presence_penalty: Optional[float] = Field(default=None)
    verbosity: Optional[str] = Field(default=None, max_length=16)
    parallel_tool_calls: Optional[bool] = Field(default=None)
    provider_tested: bool = Field(default=False)
    tool_calling_tested: bool = Field(default=False)
    streaming_tested: bool = Field(default=False)
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )


class AIConversation(SQLModel, table=True):
    __tablename__ = "ai_conversations"

    id: str = Field(default_factory=_uuid, primary_key=True, max_length=36)
    user_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    server_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("servers.id", ondelete="SET NULL"), nullable=True, index=True
        ),
    )
    title: str = Field(default="New conversation", max_length=255)
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )


class AIMessage(SQLModel, table=True):
    __tablename__ = "ai_messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: str = Field(
        max_length=36,
        sa_column=Column(
            String(36),
            ForeignKey("ai_conversations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    role: str = Field(max_length=16)
    content: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    tool_calls: Optional[list] = Field(default=None, sa_column=Column(JSON, nullable=True))
    tool_call_id: Optional[str] = Field(default=None, max_length=100)
    tool_name: Optional[str] = Field(default=None, max_length=100)
    visible: bool = Field(default=True)
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )


class AIRun(SQLModel, table=True):
    __tablename__ = "ai_runs"

    id: str = Field(default_factory=_uuid, primary_key=True, max_length=36)
    conversation_id: str = Field(
        max_length=36,
        sa_column=Column(
            String(36),
            ForeignKey("ai_conversations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    user_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    server_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("servers.id", ondelete="SET NULL"), nullable=True),
    )
    status: str = Field(default="queued", max_length=32, index=True)
    error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )
    completed_at: Optional[datetime] = Field(default=None)


class AIToolRun(SQLModel, table=True):
    __tablename__ = "ai_tool_runs"
    __table_args__ = (UniqueConstraint("run_id", "tool_call_id", name="uq_ai_tool_run_call"),)

    id: str = Field(default_factory=_uuid, primary_key=True, max_length=36)
    run_id: str = Field(
        max_length=36,
        sa_column=Column(
            String(36),
            ForeignKey("ai_runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    tool_call_id: str = Field(max_length=100)
    tool_name: str = Field(max_length=100)
    arguments: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    arguments_hash: str = Field(max_length=64)
    risk: str = Field(default="read", max_length=16)
    status: str = Field(default="pending", max_length=32)
    requires_approval: bool = Field(default=False)
    approved_by: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    approved_at: Optional[datetime] = Field(default=None)
    approval_expires_at: Optional[datetime] = Field(default=None, index=True)
    plan_snapshot: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    progress_snapshot: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    progress_updated_at: Optional[datetime] = Field(default=None)
    result: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    completed_at: Optional[datetime] = Field(default=None)
