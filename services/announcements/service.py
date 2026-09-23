"""Persistence operations for published and administrator-managed notices."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from modules.models import Announcement

from .types import AnnouncementRecord

PUBLIC_LIMIT = 50
ADMIN_LIMIT = 200


def _record(item: Announcement) -> AnnouncementRecord:
    if item.id is None:
        raise ValueError("Announcement must be persisted before it can be presented")
    return AnnouncementRecord(
        id=item.id,
        title=item.title,
        body_markdown=item.body_markdown,
        is_published=item.is_published,
        created_at=item.created_at,
        updated_at=item.updated_at,
        published_at=item.published_at,
    )


async def list_published_announcements(db: AsyncSession) -> list[AnnouncementRecord]:
    result = await db.execute(
        select(Announcement)
        .where(col(Announcement.is_published).is_(True))
        .order_by(
            col(Announcement.published_at).desc(),
            col(Announcement.id).desc(),
        )
        .limit(PUBLIC_LIMIT)
    )
    return [_record(item) for item in result.scalars().all()]


async def list_admin_announcements(db: AsyncSession) -> list[AnnouncementRecord]:
    result = await db.execute(
        select(Announcement)
        .order_by(
            col(Announcement.updated_at).desc(),
            col(Announcement.id).desc(),
        )
        .limit(ADMIN_LIMIT)
    )
    return [_record(item) for item in result.scalars().all()]


async def create_announcement(
    db: AsyncSession,
    *,
    title: str,
    body_markdown: str,
    is_published: bool,
    actor_user_id: int,
) -> AnnouncementRecord:
    now = datetime.now(UTC)
    published_at = now if is_published else None
    item = Announcement(
        title=title,
        body_markdown=body_markdown,
        is_published=is_published,
        created_by_user_id=actor_user_id,
        created_at=now,
        updated_at=now,
        published_at=published_at,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return _record(item)


async def update_announcement(
    db: AsyncSession,
    announcement_id: int,
    *,
    title: str,
    body_markdown: str,
    is_published: bool,
) -> AnnouncementRecord | None:
    item = await db.get(Announcement, announcement_id)
    if item is None:
        return None
    if is_published and not item.is_published:
        item.published_at = datetime.now(UTC)
    item.title = title
    item.body_markdown = body_markdown
    item.is_published = is_published
    item.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(item)
    return _record(item)


async def delete_announcement(db: AsyncSession, announcement_id: int) -> bool:
    item = await db.get(Announcement, announcement_id)
    if item is None:
        return False
    await db.delete(item)
    await db.commit()
    return True
