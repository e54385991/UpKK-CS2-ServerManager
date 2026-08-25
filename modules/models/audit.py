"""Immutable administrator audit records."""

# ruff: noqa: F403,F405

import uuid

from .common import *


def _uuid() -> str:
    return str(uuid.uuid4())


class AuditLog(SQLModel, table=True):
    """Metadata-only audit event retained for the last 30 days."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_category_created", "category", "created_at"),
        Index("ix_audit_logs_actor_created", "actor_user_id", "created_at"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True, max_length=36)
    created_at: Optional[datetime] = Field(
        default=None,
        index=True,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")},
    )
    category: str = Field(max_length=32)
    action: str = Field(max_length=100)
    status: str = Field(max_length=32)
    actor_user_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    actor_username: Optional[str] = Field(default=None, max_length=100)
    actor_external_id: Optional[str] = Field(default=None, max_length=32)
    ip_address: Optional[str] = Field(default=None, max_length=64)
    user_agent: Optional[str] = Field(default=None, max_length=500)
    source: str = Field(default="web", max_length=32)
    server_id: Optional[int] = Field(default=None, index=True)
    details: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
