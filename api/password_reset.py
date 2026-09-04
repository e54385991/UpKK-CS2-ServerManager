"""Shared password-reset flow for legacy ``/api/auth`` and ``/api/v1/auth``."""

from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.legacy_html import console_public_url, legacy_html_mode
from modules import (
    PasswordResetToken,
    User,
    generate_api_key,
    get_current_time,
    get_password_hash_async,
    settings,
)
from services.audit_log_service import record_audit_event
from services.captcha_policy import require_captcha

# Kept as a compatibility alias for integrations that patch the legacy service directly.
from services.captcha_service import captcha_service  # noqa: F401
from services.email_service import email_service
from services.rate_limit import enforce_rate_limit

GENERIC_FORGOT_MESSAGE = (
    "If an account with this email exists, a password reset link has been sent."
)
RESET_SUCCESS_MESSAGE = "Password reset successfully. You can now log in with your new password."


def build_password_reset_link(token: str) -> str:
    """Point reset emails at Next unless this listener still serves Jinja."""
    query = f"token={token}"
    if legacy_html_mode() == "serve":
        return f"{settings.BACKEND_URL.rstrip('/')}/reset-password?{query}"
    return console_public_url("/reset-password", query)


async def request_password_reset(
    *,
    email: str,
    captcha_token: str | None,
    captcha_code: str | None,
    request: Request,
    db: AsyncSession,
) -> dict[str, bool | str]:
    await enforce_rate_limit(request, "forgot_password", limit=5, window=3600, identity=email)
    await require_captcha(db, captcha_token, captcha_code)

    await record_audit_event(
        category="auth",
        action="password_reset.request",
        status="success",
        request=request,
        details={"email": email},
    )

    user = await User.get_by_email(db, email)
    # Always return the same body so the form cannot enumerate accounts.
    if not user:
        return {"success": True, "message": GENERIC_FORGOT_MESSAGE}

    reset_token = generate_api_key()
    expires_at = get_current_time() + timedelta(hours=1)
    await PasswordResetToken.create_token(db, user.id, reset_token, expires_at)

    html_content, text_content = email_service.get_password_reset_template(
        build_password_reset_link(reset_token),
        user.username,
    )
    email_sent = await email_service.send_email(
        db,
        user.email,
        "Password Reset Request - CS2 Server Manager",
        html_content,
        text_content,
    )
    if not email_sent:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send password reset email. Please contact administrator.",
        )

    return {"success": True, "message": GENERIC_FORGOT_MESSAGE}


async def complete_password_reset(
    *,
    token: str,
    new_password: str,
    request: Request,
    db: AsyncSession,
) -> dict[str, bool | str]:
    await enforce_rate_limit(request, "reset_password", limit=10, window=3600)
    reset = await PasswordResetToken.get_by_token(db, token)
    if reset is None or not reset.is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    user = await db.get(User, reset.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.hashed_password = await get_password_hash_async(new_password)
    reset.used = True
    db.add(user)
    db.add(reset)
    await db.commit()
    await record_audit_event(
        category="auth",
        action="password_reset.complete",
        status="success",
        user=user,
        request=request,
    )
    return {"success": True, "message": RESET_SUCCESS_MESSAGE}
