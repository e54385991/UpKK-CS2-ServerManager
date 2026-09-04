"""System-wide CAPTCHA policy and validation helpers."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from modules.models.system import SystemSettings
from services.captcha_service import captcha_service


async def captcha_is_enabled(db: AsyncSession) -> bool:
    """Read the persisted policy, failing closed when the setting is unavailable."""
    # Some isolated unit-test doubles do not expose a database execute method.
    # Treat those as the safe default while production sessions always support it.
    if not callable(getattr(db, "execute", None)):
        return True
    try:
        settings = await SystemSettings.get_settings(db)
    except Exception:
        # A migration race or a temporary database failure must never turn the
        # policy into an authentication bypass. Keep CAPTCHA enabled instead.
        return True
    return settings is None or bool(settings.captcha_enabled)


async def require_captcha(
    db: AsyncSession,
    token: str | None,
    code: str | None,
) -> None:
    """Validate CAPTCHA only while the administrator policy is enabled."""
    if not await captcha_is_enabled(db):
        return
    if not token or not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CAPTCHA is required",
        )
    if not await captcha_service.validate_captcha(token, code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired CAPTCHA code",
        )
