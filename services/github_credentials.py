"""Resolve GitHub credentials without exposing the global fallback token."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from modules.models import SystemSettings, User


async def get_effective_github_token(
    db: AsyncSession,
    user: User,
) -> Optional[str]:
    """Return the user's token, falling back to the system-wide token.

    A personal token always wins so users can access repositories granted only
    to their own account.  The global credential is deliberately read only when
    the personal setting is blank.
    """

    personal_token = (getattr(user, "github_token", None) or "").strip()
    if personal_token:
        return personal_token

    settings = await SystemSettings.get_settings(db)
    global_token = (
        getattr(settings, "global_github_token", None) if settings else None
    )
    normalized_token = (global_token or "").strip()
    return normalized_token or None
