"""CS2 public-version change tracking and three-day notice boundaries."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from modules.models import CS2VersionState
from services import cs2_version_tracking as tracking

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def session_factory(db):
    @asynccontextmanager
    async def context():
        yield db

    return context


@pytest.mark.asyncio
async def test_first_version_observation_seeds_baseline_without_starting_notice(monkeypatch):
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=None),
        add=Mock(),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(tracking, "async_session_maker", session_factory(db))
    monkeypatch.setattr(tracking, "get_current_time", lambda: NOW)

    assert await tracking.remember_advertised_version("1.42.0.1")

    state = db.add.call_args.args[0]
    assert state.id == 1
    assert state.advertised_version == "1.42.0.1"
    assert state.version_changed_at is None
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_build_representation_does_not_refresh_update_timestamp(monkeypatch):
    state = CS2VersionState(
        id=1,
        advertised_version="1.42.0.1",
        version_changed_at=NOW - timedelta(days=4),
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=state), commit=AsyncMock())
    monkeypatch.setattr(tracking, "async_session_maker", session_factory(db))

    assert await tracking.remember_advertised_version("14201")

    assert state.version_changed_at == NOW - timedelta(days=4)
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_build_starts_a_fresh_notice_window(monkeypatch):
    state = CS2VersionState(id=1, advertised_version="1.42.0.1")
    db = SimpleNamespace(scalar=AsyncMock(return_value=state), commit=AsyncMock())
    monkeypatch.setattr(tracking, "async_session_maker", session_factory(db))
    monkeypatch.setattr(tracking, "get_current_time", lambda: NOW)

    assert await tracking.remember_advertised_version("1.42.0.2")

    assert state.advertised_version == "1.42.0.2"
    assert state.version_changed_at == NOW
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recent_notice_expires_at_exactly_72_hours():
    state = CS2VersionState(
        id=1,
        advertised_version="1.42.0.2",
        version_changed_at=NOW - timedelta(hours=71),
    )
    db = SimpleNamespace(get=AsyncMock(return_value=state))

    notice = await tracking.get_recent_update_notice(db, now=NOW)
    assert notice is not None
    assert notice.version == "1.42.0.2"
    assert notice.changed_at == NOW - timedelta(hours=71)
    assert notice.expires_at == notice.changed_at + timedelta(days=3)

    state.version_changed_at = NOW - timedelta(hours=72)
    assert await tracking.get_recent_update_notice(db, now=NOW) is None


@pytest.mark.asyncio
async def test_public_version_monitor_checks_on_a_bounded_interval(monkeypatch):
    from services import auto_update_service as auto_update_module

    service = auto_update_module.AutoUpdateService()
    check = AsyncMock(return_value=(True, {"required_version": "1.42.0.3"}))
    remember = AsyncMock(return_value=True)
    monkeypatch.setattr(auto_update_module.steam_api_service, "check_version", check)
    monkeypatch.setattr(auto_update_module, "remember_advertised_version", remember)

    await service._check_public_cs2_version()
    await service._check_public_cs2_version()

    check.assert_awaited_once_with(timeout=8, retries=1, use_cache=True)
    remember.assert_awaited_once_with("1.42.0.3")
