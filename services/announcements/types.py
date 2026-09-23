"""Transport-independent announcement service values."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AnnouncementRecord:
    id: int
    title: str
    body_markdown: str
    is_published: bool
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
