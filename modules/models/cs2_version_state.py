"""Singleton state for detecting public CS2 version changes."""

# ruff: noqa: F403,F405

from sqlalchemy import DateTime

from .common import *


class CS2VersionState(SQLModel, table=True):
    """The most recently observed Steam build and when it changed."""

    __tablename__: ClassVar[str] = "cs2_version_state"
    __table_args__ = (CheckConstraint("id = 1", name="cs2_version_state_singleton"),)

    id: int = Field(default=1, primary_key=True)
    advertised_version: Optional[str] = Field(default=None, max_length=50)
    version_changed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
