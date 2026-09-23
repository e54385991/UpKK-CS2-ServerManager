"""Administrator-authored announcements shown in the console."""

# ruff: noqa: F403,F405

from sqlalchemy import DateTime

from .common import *


class Announcement(SQLModel, table=True):
    """A Markdown announcement with an explicit draft/published state."""

    __tablename__: ClassVar[str] = "announcements"
    __table_args__ = (Index("ix_announcements_published_at", "is_published", "published_at"),)

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(max_length=160, nullable=False)
    body_markdown: str = Field(sa_column=Column(Text, nullable=False))
    is_published: bool = Field(
        default=False,
        nullable=False,
        sa_column_kwargs={"server_default": text("false")},
    )
    created_by_user_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        )
    )
    published_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
