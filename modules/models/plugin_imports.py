"""Durable global marketplace import jobs."""

from datetime import datetime
from typing import ClassVar
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime
from sqlmodel import Field, SQLModel


class PluginImportJob(SQLModel, table=True):
    __tablename__: ClassVar[str] = "plugin_import_jobs"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, max_length=36)
    actor_user_id: int = Field(foreign_key="users.id", index=True)
    request_key: str = Field(unique=True, max_length=100)
    status: str = Field(default="queued", max_length=24, index=True)
    options: dict[str, object] = Field(sa_column=Column(JSON, nullable=False))
    command: str = Field(max_length=1000)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    completed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    heartbeat_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    cancel_requested: bool = False
    phase: str = Field(default="queued", max_length=40)
    message: str = Field(default="", max_length=2000)
    current_repository: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=255)
    stop_reason: str | None = Field(default=None, max_length=40)
    retry_at: int | None = None
    items: list[dict[str, object]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    events: list[dict[str, object]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
