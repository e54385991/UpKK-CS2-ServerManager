"""Google OAuth client ID used by the public login popup."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from modules.config import get_settings
from modules.models import SystemSettings


def environment_google_client_id() -> str:
    return (get_settings().GOOGLE_CLIENT_ID or "").strip()


def stored_google_client_id(settings: SystemSettings | None) -> str:
    return ((getattr(settings, "google_client_id", None) if settings else None) or "").strip()


def effective_google_client_id(settings: SystemSettings | None) -> str:
    return stored_google_client_id(settings) or environment_google_client_id()


async def resolve_google_client_id(db: AsyncSession) -> str:
    row = await SystemSettings.get_settings(db)
    return effective_google_client_id(row)
