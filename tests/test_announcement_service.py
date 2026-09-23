"""Persistence behavior for announcement publication transitions."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from modules.models import Announcement
from services.announcements import service

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def persisted_announcement(
    *, announcement_id: int | None = 1, published: bool = False
) -> Announcement:
    return Announcement(
        id=announcement_id,
        title="Notice",
        body_markdown="**Hello**",
        is_published=published,
        created_at=NOW,
        updated_at=NOW,
        published_at=NOW if published else None,
    )


@pytest.mark.asyncio
async def test_lists_map_persisted_models_to_transport_independent_records():
    item = persisted_announcement(published=True)
    result = MagicMock()
    result.scalars.return_value.all.return_value = [item]
    db = SimpleNamespace(execute=AsyncMock(return_value=result))

    public = await service.list_published_announcements(db)
    admin = await service.list_admin_announcements(db)

    assert public == [service._record(item)]
    assert admin[0].body_markdown == "**Hello**"
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_create_sets_publication_time_only_for_published_items():
    db = SimpleNamespace(
        add=Mock(),
        commit=AsyncMock(),
        refresh=AsyncMock(side_effect=lambda item: setattr(item, "id", 3)),
    )

    published = await service.create_announcement(
        db,
        title="Notice",
        body_markdown="Text",
        is_published=True,
        actor_user_id=7,
    )
    draft = await service.create_announcement(
        db,
        title="Draft",
        body_markdown="Text",
        is_published=False,
        actor_user_id=7,
    )

    published_model = db.add.call_args_list[0].args[0]
    draft_model = db.add.call_args_list[1].args[0]
    assert published.is_published and published.published_at is not None
    assert published_model.created_by_user_id == 7
    assert draft.published_at is None and draft_model.published_at is None


@pytest.mark.asyncio
async def test_update_handles_missing_and_publication_transitions():
    db = SimpleNamespace(get=AsyncMock(return_value=None))
    assert (
        await service.update_announcement(
            db,
            99,
            title="Missing",
            body_markdown="Text",
            is_published=True,
        )
        is None
    )

    item = persisted_announcement(published=False)
    db.get.return_value = item
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    published = await service.update_announcement(
        db,
        1,
        title="Published",
        body_markdown="Updated",
        is_published=True,
    )
    assert published is not None and published.published_at is not None

    publication_time = item.published_at
    draft = await service.update_announcement(
        db,
        1,
        title="Draft again",
        body_markdown="Updated",
        is_published=False,
    )
    assert draft is not None and not draft.is_published
    assert draft.published_at == publication_time


@pytest.mark.asyncio
async def test_delete_reports_missing_and_commits_existing_row():
    db = SimpleNamespace(get=AsyncMock(return_value=None))
    assert not await service.delete_announcement(db, 9)

    item = persisted_announcement()
    db.get.return_value = item
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    assert await service.delete_announcement(db, 1)
    db.delete.assert_awaited_once_with(item)
    db.commit.assert_awaited_once()


def test_unpersisted_model_is_not_presentable():
    with pytest.raises(ValueError, match="must be persisted"):
        service._record(persisted_announcement(announcement_id=None))
