"""Versioned personal-center endpoints (non-secret profile + credentials)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from sqlmodel import col

from api.dependencies import ActiveUser, DatabaseSession
from api.routes import ai as legacy_ai
from modules import (
    User,
    generate_api_key,
    get_password_hash_async,
    verify_password_async,
)
from modules.schemas.ai import AIProviderTestRequest, UserAISettingsUpdate
from services.audit_log_service import record_audit_event
from services.captcha_policy import require_captcha

# Kept as a compatibility alias for integrations that patch the legacy service directly.
from services.captcha_service import captcha_service  # noqa: F401
from services.s3_backup_service import s3_backup_service
from services.steam_api_service import steam_api_service
from services.steamcmd_retry import (
    STEAMCMD_DEFAULT_MAX_RETRIES,
    STEAMCMD_MAX_RETRIES_LIMIT,
    clamp_steamcmd_max_retries,
)

from .schemas import (
    ActionResult,
    AssistantProviderTestBody,
    AssistantProviderTestView,
    AssistantUserSettingsPatch,
    AssistantUserSettingsView,
    ProfileApiKeyGenerate,
    ProfileApiKeyView,
    ProfileGsltGenerate,
    ProfileGsltView,
    ProfilePasswordChange,
    ProfilePatch,
    ProfileS3Patch,
    ProfileS3TestStep,
    ProfileS3TestView,
    ProfileS3View,
    ProfileView,
)

router = APIRouter(prefix="/api/v1/profile", tags=["v1-profile"])

_SENSITIVE_PROFILE_FIELDS = frozenset(
    {
        "email",
        "steam_api_key",
        "clear_steam_api_key",
        "github_token",
        "clear_github_token",
    }
)


def _secret_prefix(value: str | None, *, length: int = 8) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    return f"{text[:length]}..."


def to_view(user) -> ProfileView:
    """Project the authenticated user without secrets."""
    steam_key = getattr(user, "steam_api_key", None)
    github_token = getattr(user, "github_token", None)
    api_key = getattr(user, "api_key", None)
    return ProfileView(
        id=user.id,
        username=user.username,
        email=getattr(user, "email", None),
        is_admin=bool(getattr(user, "is_admin", False)),
        is_active=bool(getattr(user, "is_active", True)),
        created_at=getattr(user, "created_at", None),
        steamcmd_max_retries=clamp_steamcmd_max_retries(
            getattr(user, "steamcmd_max_retries", None)
        ),
        steamcmd_max_retries_default=STEAMCMD_DEFAULT_MAX_RETRIES,
        steamcmd_max_retries_limit=STEAMCMD_MAX_RETRIES_LIMIT,
        has_steam_api_key=bool((steam_key or "").strip()),
        steam_api_key_prefix=_secret_prefix(steam_key),
        has_github_token=bool((github_token or "").strip()),
        github_token_prefix=_secret_prefix(github_token, length=12),
        has_api_key=bool((api_key or "").strip()),
    )


def to_s3_view(user) -> ProfileS3View:
    return ProfileS3View(
        enabled=bool(getattr(user, "s3_enabled", False)),
        endpoint_url=getattr(user, "s3_endpoint_url", None),
        region=getattr(user, "s3_region", None),
        bucket=getattr(user, "s3_bucket", None),
        access_key_id=getattr(user, "s3_access_key_id", None),
        prefix=getattr(user, "s3_prefix", None),
        use_ssl=bool(getattr(user, "s3_use_ssl", True)),
        retention_count=s3_backup_service.get_retention_count(user),
        has_secret=bool(getattr(user, "s3_secret_access_key", None)),
        is_configured=s3_backup_service.is_configured(user),
    )


def _user_ai_view(payload) -> AssistantUserSettingsView:
    return AssistantUserSettingsView(
        mode=payload.mode,
        base_url=payload.base_url,
        model=payload.model,
        api_protocol=payload.api_protocol,
        api_key_configured=bool(payload.api_key_configured),
        reasoning_effort=getattr(payload, "reasoning_effort", None),
        temperature=getattr(payload, "temperature", None),
        top_p=getattr(payload, "top_p", None),
        max_completion_tokens=int(getattr(payload, "max_completion_tokens", 2048) or 2048),
        token_limit_parameter=getattr(payload, "token_limit_parameter", None)
        or "max_completion_tokens",
        frequency_penalty=getattr(payload, "frequency_penalty", None),
        presence_penalty=getattr(payload, "presence_penalty", None),
        verbosity=getattr(payload, "verbosity", None),
        parallel_tool_calls=getattr(payload, "parallel_tool_calls", None),
        provider_tested=bool(payload.provider_tested),
        tool_calling_tested=bool(payload.tool_calling_tested),
        streaming_tested=bool(payload.streaming_tested),
        effective_enabled=bool(payload.effective_enabled),
        effective_source=payload.effective_source,
    )


@router.get("", response_model=ProfileView)
async def read_profile(current_user: ActiveUser) -> ProfileView:
    """Return account identity plus the SteamCMD auto-recovery budget."""
    return to_view(current_user)


@router.patch("", response_model=ProfileView)
async def update_profile(
    patch: ProfilePatch,
    current_user: ActiveUser,
    db: DatabaseSession,
    request: Request,
) -> ProfileView:
    """Persist personal-center fields. Secrets are write-only."""
    fields_set = set(patch.model_fields_set)
    if fields_set & _SENSITIVE_PROFILE_FIELDS:
        await require_captcha(db, patch.captcha_token, patch.captcha_code)

    changed: list[str] = []
    if patch.steamcmd_max_retries is not None:
        current_user.steamcmd_max_retries = clamp_steamcmd_max_retries(patch.steamcmd_max_retries)
        changed.append("steamcmd_max_retries")

    if patch.email is not None:
        result = await db.execute(
            select(User).where(col(User.email) == patch.email, col(User.id) != current_user.id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered by another user",
            )
        current_user.email = patch.email
        changed.append("email")

    if patch.clear_steam_api_key:
        current_user.steam_api_key = None
        changed.append("steam_api_key")
    elif patch.steam_api_key is not None:
        current_user.steam_api_key = patch.steam_api_key.strip() or None
        changed.append("steam_api_key")

    if patch.clear_github_token:
        current_user.github_token = None
        changed.append("github_token")
    elif patch.github_token is not None:
        current_user.github_token = patch.github_token.strip() or None
        changed.append("github_token")

    if not changed:
        return to_view(current_user)

    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    await record_audit_event(
        category="settings",
        action="profile.update",
        status="success",
        user=current_user,
        request=request,
        details={"changed_fields": changed},
    )
    return to_view(current_user)


@router.post("/password", response_model=ActionResult)
async def change_password(
    body: ProfilePasswordChange,
    current_user: ActiveUser,
    db: DatabaseSession,
    request: Request,
) -> ActionResult:
    """Replace the signed-in user's password after captcha + current-password check."""
    await require_captcha(db, body.captcha_token, body.captcha_code)
    if not await verify_password_async(body.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    if body.new_password != body.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password and confirm password do not match",
        )
    current_user.hashed_password = await get_password_hash_async(body.new_password)
    db.add(current_user)
    await db.commit()
    await record_audit_event(
        category="auth",
        action="password_change",
        status="success",
        user=current_user,
        request=request,
    )
    return ActionResult(success=True, message="Password reset successfully")


@router.get("/api-key", response_model=ProfileApiKeyView)
async def read_api_key(current_user: ActiveUser) -> ProfileApiKeyView:
    """Reveal the user's own panel API key so they can copy it."""
    if not current_user.api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No API key generated. Please generate one first.",
        )
    return ProfileApiKeyView(api_key=current_user.api_key, created_at=current_user.updated_at)


@router.post("/api-key", response_model=ProfileApiKeyView)
async def generate_user_api_key(
    body: ProfileApiKeyGenerate,
    current_user: ActiveUser,
    db: DatabaseSession,
    request: Request,
) -> ProfileApiKeyView:
    """Generate or rotate the personal API key."""
    if body.captcha_token or body.captcha_code:
        await require_captcha(db, body.captcha_token, body.captcha_code)

    new_api_key = generate_api_key()
    for _ in range(5):
        existing = await User.get_by_api_key(db, new_api_key)
        if not existing:
            break
        new_api_key = generate_api_key()
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate unique API key. Please try again.",
        )

    current_user.api_key = new_api_key
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    await record_audit_event(
        category="settings",
        action="api_key.generate",
        status="success",
        user=current_user,
        request=request,
    )
    return ProfileApiKeyView(api_key=current_user.api_key, created_at=current_user.updated_at)


@router.delete("/api-key", response_model=ActionResult)
async def revoke_api_key(
    current_user: ActiveUser,
    db: DatabaseSession,
    request: Request,
) -> ActionResult:
    """Revoke the personal API key."""
    if not current_user.api_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No API key to revoke")
    current_user.api_key = None
    db.add(current_user)
    await db.commit()
    await record_audit_event(
        category="settings",
        action="api_key.revoke",
        status="success",
        user=current_user,
        request=request,
    )
    return ActionResult(success=True, message="API key revoked successfully")


@router.post("/gslt", response_model=ProfileGsltView)
async def generate_gslt(
    body: ProfileGsltGenerate,
    current_user: ActiveUser,
    db: DatabaseSession,
    request: Request,
) -> ProfileGsltView:
    """Create a Steam game server login token using the user's Steam Web API key."""
    await require_captcha(db, body.captcha_token, body.captcha_code)
    steam_key = (getattr(current_user, "steam_api_key", None) or "").strip()
    if not steam_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Steam API key not set. Please set your Steam API key in profile settings first."
            ),
        )

    if body.server_name and isinstance(body.server_name, str):
        memo = body.server_name.strip() or f"CS2 Server - {current_user.username}"
    else:
        memo = f"CS2 Server - {current_user.username}"

    success, result = await steam_api_service.create_game_server_account(
        steam_api_key=steam_key, memo=memo
    )
    login_token = (result or {}).get("login_token") if result else None
    if not success or not login_token:
        error_msg = ((result or {}).get("error") if result else None) or "Failed to generate token"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)

    steamid = (result or {}).get("steamid") or None
    await record_audit_event(
        category="settings",
        action="gslt.generate",
        status="success",
        user=current_user,
        request=request,
        details={"memo": memo},
    )
    return ProfileGsltView(
        login_token=str(login_token),
        steamid=str(steamid) if steamid else None,
    )


@router.get("/s3", response_model=ProfileS3View)
async def read_s3_settings(current_user: ActiveUser) -> ProfileS3View:
    """Return S3 backup settings with the secret replaced by a presence flag."""
    return to_s3_view(current_user)


@router.put("/s3", response_model=ProfileS3View)
async def update_s3_settings(
    body: ProfileS3Patch,
    current_user: ActiveUser,
    db: DatabaseSession,
    request: Request,
) -> ProfileS3View:
    """Persist S3-compatible backup settings. The secret is write-only."""
    await require_captcha(db, body.captcha_token, body.captcha_code)
    if body.enabled is not None:
        current_user.s3_enabled = body.enabled
    if body.endpoint_url is not None:
        current_user.s3_endpoint_url = body.endpoint_url or None
    if body.region is not None:
        current_user.s3_region = body.region or None
    if body.bucket is not None:
        current_user.s3_bucket = body.bucket or None
    if body.access_key_id is not None:
        current_user.s3_access_key_id = body.access_key_id or None
    if body.prefix is not None:
        current_user.s3_prefix = body.prefix or None
    if body.use_ssl is not None:
        current_user.s3_use_ssl = body.use_ssl
    if body.retention_count is not None:
        current_user.s3_retention_count = body.retention_count
    if body.clear_secret:
        current_user.s3_secret_access_key = None
    elif body.secret_access_key:
        current_user.s3_secret_access_key = body.secret_access_key

    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    await record_audit_event(
        category="settings",
        action="s3.update",
        status="success",
        user=current_user,
        request=request,
        details={
            "changed_fields": [
                name
                for name in (
                    "enabled",
                    "endpoint_url",
                    "region",
                    "bucket",
                    "access_key_id",
                    "prefix",
                    "use_ssl",
                    "retention_count",
                    "clear_secret",
                    "secret_access_key",
                )
                if name in body.model_fields_set
            ]
        },
    )
    return to_s3_view(current_user)


@router.post("/s3/test", response_model=ProfileS3TestView)
async def test_s3_settings(current_user: ActiveUser) -> ProfileS3TestView:
    """Exercise list/upload/download/delete against the saved S3 configuration."""
    success, message, steps = await s3_backup_service.test_connection(current_user)
    return ProfileS3TestView(
        success=success,
        message=message,
        steps=[ProfileS3TestStep(**step) for step in steps],
    )


@router.get("/ai", response_model=AssistantUserSettingsView)
async def read_user_ai_settings(
    db: DatabaseSession, current_user: ActiveUser
) -> AssistantUserSettingsView:
    return _user_ai_view(await legacy_ai.get_user_ai_settings(db, current_user))


@router.put("/ai", response_model=AssistantUserSettingsView)
async def update_user_ai_settings(
    body: AssistantUserSettingsPatch,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> AssistantUserSettingsView:
    payload = body.model_dump(exclude_unset=True)
    if "mode" not in payload:
        current = await legacy_ai.get_user_ai_settings(db, current_user)
        payload["mode"] = current.mode
    return _user_ai_view(
        await legacy_ai.update_user_ai_settings(
            UserAISettingsUpdate(**payload),
            db,
            current_user,
        )
    )


@router.post("/ai/test", response_model=AssistantProviderTestView)
async def test_user_ai_settings(
    body: AssistantProviderTestBody,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> AssistantProviderTestView:
    payload = await legacy_ai.test_user_ai_settings(
        AIProviderTestRequest(**body.model_dump(exclude_unset=True)),
        db,
        current_user,
    )
    return AssistantProviderTestView(
        success=bool(payload.success),
        text_response_ok=bool(payload.text_response_ok),
        tool_calling_ok=bool(payload.tool_calling_ok),
        streaming_ok=bool(payload.streaming_ok),
        message=str(payload.message),
    )
