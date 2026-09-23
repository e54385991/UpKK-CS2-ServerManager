"""Persist Steam's public CS2 version and expose a short-lived update notice."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from modules.database import async_session_maker
from modules.models import CS2VersionState
from modules.utils import get_current_time

logger = logging.getLogger(__name__)
CS2_UPDATE_NOTICE_TTL = timedelta(days=3)


@dataclass(frozen=True, slots=True)
class CS2VersionUpdateNotice:
    version: str
    changed_at: datetime
    expires_at: datetime


def _version_digits(version: str) -> str:
    return "".join(character for character in version if character.isdigit())


def versions_equivalent(left: str, right: str) -> bool:
    """Treat Steam's dotted and numeric representations of one build equally."""
    left_digits = _version_digits(left)
    right_digits = _version_digits(right)
    return bool(left_digits and right_digits and left_digits == right_digits)


async def remember_advertised_version(version: str) -> bool:
    """Record a version change; the first observation only establishes baseline.

    Returns false on persistence failure so the Steam checker can retry later
    without making update detection a prerequisite for game-version checks.
    """
    candidate = version.strip()
    if not candidate or not _version_digits(candidate):
        return True

    try:
        async with async_session_maker() as db:
            state = await db.scalar(
                select(CS2VersionState).where(CS2VersionState.id == 1).with_for_update()
            )
            if state is None:
                state = CS2VersionState(id=1, advertised_version=candidate)
                db.add(state)
            elif not state.advertised_version:
                state.advertised_version = candidate
            elif not versions_equivalent(state.advertised_version, candidate):
                state.advertised_version = candidate
                state.version_changed_at = get_current_time()
            else:
                return True
            await db.commit()
        return True
    except Exception:
        logger.exception("Failed to persist the advertised CS2 version")
        return False


async def get_recent_update_notice(
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> CS2VersionUpdateNotice | None:
    """Return a notice only while a newly observed version is under 72 hours old."""
    state = await db.get(CS2VersionState, 1)
    if state is None or not state.advertised_version or state.version_changed_at is None:
        return None

    current_time = now or get_current_time()
    changed_at = state.version_changed_at
    if changed_at.tzinfo is None:
        changed_at = changed_at.replace(tzinfo=current_time.tzinfo)
    age = current_time - changed_at
    if age < timedelta(0) or age >= CS2_UPDATE_NOTICE_TTL:
        return None

    return CS2VersionUpdateNotice(
        version=state.advertised_version,
        changed_at=changed_at,
        expires_at=changed_at + CS2_UPDATE_NOTICE_TTL,
    )
