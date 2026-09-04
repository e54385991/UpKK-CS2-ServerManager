"""Shared registration flow for legacy ``/api/auth`` and ``/api/v1/auth``."""

from __future__ import annotations

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from modules import User, get_password_hash_async
from services.audit_log_service import record_audit_event
from services.captcha_policy import require_captcha

# Kept as a compatibility alias for integrations that patch the legacy service directly.
from services.captcha_service import captcha_service  # noqa: F401
from services.rate_limit import enforce_rate_limit


async def register_user(
    *,
    username: str,
    email: str,
    password: str,
    captcha_token: str | None,
    captcha_code: str | None,
    request: Request,
    db: AsyncSession,
) -> User:
    """Create a non-admin member after CAPTCHA and uniqueness checks.

    The first administrator is seeded by ``init_db`` (admin/admin123). Open
    self-registration always creates a regular account; there is no invite or
    first-user wizard on this path.
    """
    await enforce_rate_limit(request, "register", limit=5, window=3600, identity=username)
    await require_captcha(db, captcha_token, captcha_code)

    existing_user = await User.get_by_username(db, username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered",
        )

    existing_email = await User.get_by_email(db, email)
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    await db.commit()
    hashed_password = await get_password_hash_async(password)
    user = User(username=username, email=email, hashed_password=hashed_password)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await record_audit_event(
        category="auth",
        action="register",
        status="success",
        user=user,
        request=request,
        details={"username": user.username},
    )
    return user
