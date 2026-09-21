"""Google OAuth client ID and account binding for console sign-in."""

from __future__ import annotations

from typing import Any

from anyio import to_thread
from google.auth.transport import requests
from google.oauth2 import id_token
from sqlalchemy.ext.asyncio import AsyncSession

from modules.config import get_settings
from modules.models import SystemSettings, User


def environment_google_client_id() -> str:
    return (get_settings().GOOGLE_CLIENT_ID or "").strip()


def stored_google_client_id(settings: SystemSettings | None) -> str:
    return ((getattr(settings, "google_client_id", None) if settings else None) or "").strip()


def effective_google_client_id(settings: SystemSettings | None) -> str:
    return stored_google_client_id(settings) or environment_google_client_id()


async def resolve_google_client_id(db: AsyncSession) -> str:
    row = await SystemSettings.get_settings(db)
    return effective_google_client_id(row)


class GoogleIdentityError(Exception):
    """A verified Google identity could not be attached to the signed-in user."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def verify_google_id_token(db: AsyncSession, raw_token: str) -> dict[str, Any]:
    """Check Google's signature, audience, and expiry. The raw token is not logged."""
    client_id = await resolve_google_client_id(db)
    if not client_id:
        raise GoogleIdentityError("google_not_configured")
    try:
        idinfo = await to_thread.run_sync(
            id_token.verify_oauth2_token,
            raw_token,
            requests.Request(),
            client_id,
        )
    except ValueError as exc:
        raise GoogleIdentityError("google_invalid_token") from exc
    if not isinstance(idinfo, dict):
        raise GoogleIdentityError("google_invalid_token")
    subject = idinfo.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise GoogleIdentityError("google_invalid_token")
    return idinfo


async def bind_google_account(db: AsyncSession, user: User, raw_token: str) -> None:
    """Attach a Google subject to the signed-in panel account. The panel email stays."""
    idinfo = await verify_google_id_token(db, raw_token)
    if idinfo.get("email_verified") is not True:
        raise GoogleIdentityError("google_email_unverified")
    subject = str(idinfo["sub"])
    existing = await User.get_by_google_id(db, subject)
    if existing is not None and existing.id != user.id:
        raise GoogleIdentityError("google_account_taken")
    current = (getattr(user, "google_id", None) or "").strip()
    if current and current != subject:
        raise GoogleIdentityError("google_already_linked")
    user.google_id = subject
    user.oauth_provider = "google"
    db.add(user)
    await db.commit()
    await db.refresh(user)


async def unbind_google_account(db: AsyncSession, user: User) -> None:
    """Remove Google sign-in from this account. Password login is unchanged."""
    user.google_id = None
    if getattr(user, "oauth_provider", None) in {None, "", "google"}:
        user.oauth_provider = None
    db.add(user)
    await db.commit()
    await db.refresh(user)
