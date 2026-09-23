"""Versioned announcement contracts."""

from datetime import datetime

from pydantic import Field, field_validator

from api.contracts.base import ApiRequest
from api.contracts.v1.identity import V1Model


class AnnouncementView(V1Model):
    id: int
    title: str
    body_markdown: str
    is_published: bool
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None


class AnnouncementListView(V1Model):
    items: list[AnnouncementView]


class CS2VersionNoticeView(V1Model):
    version: str
    changed_at: datetime
    expires_at: datetime


class AnnouncementFeedView(V1Model):
    items: list[AnnouncementView]
    cs2_update_notice: CS2VersionNoticeView | None = None


class AnnouncementWrite(ApiRequest):
    title: str = Field(min_length=1, max_length=160)
    body_markdown: str = Field(min_length=1, max_length=20000)
    is_published: bool = False

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("Title cannot be blank")
        return title

    @field_validator("body_markdown")
    @classmethod
    def validate_markdown(cls, value: str) -> str:
        markdown = value.strip()
        if not markdown:
            raise ValueError("Announcement content cannot be blank")
        return markdown
