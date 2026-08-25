"""Discord Gateway configuration and server-scoped authorization state."""

# ruff: noqa: F403,F405

import uuid

from .common import *


def _uuid() -> str:
    return str(uuid.uuid4())


DEFAULT_AGENT_CAPABILITIES = [
    "inspect_status",
    "read_logs_files",
    "browse_plan_plugins",
]


class UserDiscordBot(SQLModel, table=True):
    """One encrypted Discord Bot credential per panel user."""

    __tablename__ = "user_discord_bots"
    __table_args__ = (
        CheckConstraint(
            "message_trigger_mode IN ('mention_only', 'mention_and_greetings')",
            name="ck_user_discord_bots_message_trigger_mode",
        ),
    )

    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    )
    token_encrypted: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    enabled: bool = Field(default=False)
    message_trigger_mode: str = Field(default="mention_only", max_length=32)
    global_binding_configured: bool = Field(default=False)
    global_binding_enabled: bool = Field(default=False)
    global_guild_id: Optional[str] = Field(default=None, max_length=20)
    global_channel_ids: List[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    global_role_ids: List[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    global_user_ids: List[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    global_capabilities: List[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    application_id: Optional[str] = Field(default=None, max_length=20, unique=True, index=True)
    bot_user_id: Optional[str] = Field(default=None, max_length=20, unique=True, index=True)
    username: Optional[str] = Field(default=None, max_length=100)
    discriminator: Optional[str] = Field(default=None, max_length=8)
    connection_status: str = Field(default="disabled", max_length=32)
    last_connected_at: Optional[datetime] = Field(default=None)
    last_error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )


class ServerDiscordBinding(SQLModel, table=True):
    """Discord Guild/channel allowlist and direct command capabilities for a server."""

    __tablename__ = "server_discord_bindings"

    server_id: int = Field(
        sa_column=Column(Integer, ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True)
    )
    user_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    enabled: bool = Field(default=False)
    guild_id: Optional[str] = Field(default=None, max_length=20, index=True)
    channel_ids: List[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    role_ids: List[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    user_ids: List[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    capabilities: List[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    response_visibility: str = Field(default="public", max_length=16)
    invalid_reason: Optional[str] = Field(default=None, max_length=255)
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )


class ServerAgentPolicy(SQLModel, table=True):
    """Effective AI capabilities shared by Web and Discord for one server."""

    __tablename__ = "server_agent_policies"

    server_id: int = Field(
        sa_column=Column(Integer, ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True)
    )
    enabled: bool = Field(default=True)
    capabilities: List[str] = Field(
        default_factory=lambda: list(DEFAULT_AGENT_CAPABILITIES),
        sa_column=Column(JSON, nullable=False),
    )
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )


class DiscordOperationRun(SQLModel, table=True):
    """Immutable Discord confirmation, idempotency, and execution audit record."""

    __tablename__ = "discord_operation_runs"
    __table_args__ = (
        Index("ix_discord_operation_runs_actor_created", "actor_user_id", "created_at"),
        Index("ix_discord_operation_runs_server_created", "server_id", "created_at"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True, max_length=36)
    server_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    owner_user_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    actor_user_id: str = Field(max_length=20, index=True)
    guild_id: str = Field(max_length=20)
    channel_id: str = Field(max_length=20)
    message_id: Optional[str] = Field(default=None, max_length=20)
    action: str = Field(max_length=100)
    required_capabilities: List[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    arguments: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    arguments_hash: str = Field(max_length=64)
    plan_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    plan_hash: str = Field(max_length=64)
    status: str = Field(default="pending", max_length=32, index=True)
    result: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    expires_at: datetime = Field(index=True)
    confirmed_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )
