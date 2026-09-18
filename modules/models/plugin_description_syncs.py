"""Durable global marketplace description sync jobs."""

from datetime import datetime
from typing import ClassVar
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime
from sqlmodel import Field, SQLModel


class PluginDescriptionSyncJob(SQLModel, table=True):
    __tablename__: ClassVar[str] = "plugin_description_sync_jobs"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, max_length=36)
    actor_user_id: int = Field(foreign_key="users.id", index=True)
    request_key: str = Field(unique=True, max_length=100)
    scope_key: str = Field(index=True, max_length=128)
    status: str = Field(default="queued", max_length=24, index=True)
    framework: str | None = Field(default=None, max_length=32)
    overwrite: bool = True
    command: str = Field(max_length=1000)
    target_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    cursor: int = 0
    total: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    completed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    heartbeat_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    cancel_requested: bool = False
    phase: str = Field(default="queued", max_length=40)
    message: str = Field(default="", max_length=2000)
    current_plugin_id: int | None = None
    current_plugin_title: str | None = Field(default=None, max_length=255)
    current_github_url: str | None = Field(default=None, max_length=500)
    stop_reason: str | None = Field(default=None, max_length=40)
    retry_at: int | None = None
    items: list[dict[str, object]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
