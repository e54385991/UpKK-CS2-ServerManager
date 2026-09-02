"""
Authentication routes for user registration and login
"""

import logging
from datetime import timedelta

from anyio import to_thread
from fastapi import APIRouter, HTTPException, Request, Response, status
from google.auth.transport import requests
from google.oauth2 import id_token
from sqlmodel import select

from api.dependencies import ActiveUser, DatabaseSession
from api.registration import register_user
from modules import (
    ApiKeyGenerate,
    ApiKeyResponse,
    ForgotPasswordRequest,
    GenerateServerTokenRequest,
    GenerateServerTokenResponse,
    GitHubTokenStatusResponse,
    GoogleOAuthRequest,
    PasswordReset,
    ResetPasswordRequest,
    S3SettingsResponse,
    S3SettingsUpdate,
    SteamApiKeyResponse,
    Token,
    User,
    UserCreate,
    UserLogin,
    UserProfileUpdate,
    UserResponse,
    clear_web_session_cookie,
    create_access_token,
    generate_api_key,
    get_password_hash_async,
    set_web_session_cookie,
    settings,
    verify_password_async,
)
from services.audit_log_service import INVALID_CREDENTIALS_DETAILS, record_audit_event
from services.captcha_service import captcha_service
from services.rate_limit import enforce_rate_limit
from services.s3_backup_service import s3_backup_service
from services.steam_api_service import steam_api_service

router = APIRouter(prefix="/api/auth", tags=["authentication"])


def _build_s3_settings_response(user: User) -> S3SettingsResponse:
    return S3SettingsResponse(
        enabled=bool(user.s3_enabled),
        endpoint_url=user.s3_endpoint_url,
        region=user.s3_region,
        bucket=user.s3_bucket,
        access_key_id=user.s3_access_key_id,
        prefix=user.s3_prefix,
        use_ssl=bool(user.s3_use_ssl),
        retention_count=s3_backup_service.get_retention_count(user),
        has_secret=bool(user.s3_secret_access_key),
        is_configured=s3_backup_service.is_configured(user),
    )


@router.get("/google-config")
async def get_google_config():
    """Get Google OAuth configuration (public endpoint)"""
    return {"client_id": settings.GOOGLE_CLIENT_ID, "enabled": bool(settings.GOOGLE_CLIENT_ID)}


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserCreate, request: Request, db: DatabaseSession):
    """Register a new user"""
    return await register_user(
        username=user_data.username,
        email=user_data.email,
        password=user_data.password,
        captcha_token=user_data.captcha_token,
        captcha_code=user_data.captcha_code,
        request=request,
        db=db,
    )


@router.post("/login", response_model=Token)
async def login(user_data: UserLogin, request: Request, response: Response, db: DatabaseSession):
    """Login and get access token"""
    await enforce_rate_limit(request, "login", limit=10, window=60, identity=user_data.username)
    # Validate CAPTCHA first
    is_valid = await captcha_service.validate_captcha(
        user_data.captcha_token, user_data.captcha_code
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired CAPTCHA code"
        )

    # Find user by username
    user = await User.get_by_username(db, user_data.username)
    if not user:
        await record_audit_event(
            category="auth",
            action="login",
            status="failure",
            actor_username=user_data.username,
            request=request,
            details=INVALID_CREDENTIALS_DETAILS,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    await db.commit()
    if not await verify_password_async(user_data.password, user.hashed_password):
        await record_audit_event(
            category="auth",
            action="login",
            status="failure",
            actor_user_id=user.id,
            actor_username=user.username,
            request=request,
            details=INVALID_CREDENTIALS_DETAILS,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        await record_audit_event(
            category="auth",
            action="login",
            status="failure",
            user=user,
            request=request,
            details={"reason": "inactive_user"},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")

    # Create access token
    access_token_expires = timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id), "username": user.username}, expires_delta=access_token_expires
    )
    set_web_session_cookie(request, response, access_token)
    await record_audit_event(
        category="auth",
        action="login",
        status="success",
        user=user,
        request=request,
    )

    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: ActiveUser):
    """Get current user information"""
    return current_user


@router.post("/session")
async def bootstrap_web_session(
    request: Request,
    response: Response,
    current_user: ActiveUser,
):
    """Copy an already validated bearer token into the protected browser cookie."""
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required"
        )
    set_web_session_cookie(request, response, token)
    return {"success": True}


@router.post("/reset-password")
async def reset_password(
    password_data: PasswordReset,
    request: Request,
    current_user: ActiveUser,
    db: DatabaseSession,
):
    """Reset user password"""
    # Validate CAPTCHA first
    is_valid = await captcha_service.validate_captcha(
        password_data.captcha_token, password_data.captcha_code
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired CAPTCHA code"
        )

    await db.commit()
    # Verify current password
    if not await verify_password_async(
        password_data.current_password, current_user.hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect"
        )

    # Verify new password and confirm password match
    if password_data.new_password != password_data.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password and confirm password do not match",
        )

    # Update password
    current_user.hashed_password = await get_password_hash_async(password_data.new_password)
    await db.commit()
    await record_audit_event(
        category="auth",
        action="password_change",
        status="success",
        user=current_user,
        request=request,
    )

    return {"success": True, "message": "Password reset successfully"}


@router.put("/profile", response_model=UserResponse)
async def update_profile(
    profile_data: UserProfileUpdate,
    request: Request,
    current_user: ActiveUser,
    db: DatabaseSession,
):
    """Update user profile"""
    # Validate CAPTCHA first
    is_valid = await captcha_service.validate_captcha(
        profile_data.captcha_token, profile_data.captcha_code
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired CAPTCHA code"
        )

    # Update email if provided
    if profile_data.email:
        # Check if email already exists for another user
        result = await db.execute(
            select(User).where(User.email == profile_data.email, User.id != current_user.id)
        )
        existing_email = result.scalar_one_or_none()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered by another user",
            )
        current_user.email = profile_data.email

    # Update Steam API key if provided
    if profile_data.steam_api_key is not None:
        # Allow empty string to clear the Steam API key
        if profile_data.steam_api_key.strip() == "":
            current_user.steam_api_key = None
        else:
            current_user.steam_api_key = profile_data.steam_api_key.strip()

    # Update GitHub token if provided
    if profile_data.github_token is not None:
        # Allow empty string to clear the GitHub token
        if profile_data.github_token.strip() == "":
            current_user.github_token = None
        else:
            current_user.github_token = profile_data.github_token.strip()

    changed = [
        name
        for name, present in (
            ("email", profile_data.email is not None),
            ("steam_api_key", profile_data.steam_api_key is not None),
            ("github_token", profile_data.github_token is not None),
        )
        if present
    ]
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

    return current_user


@router.get("/api-key", response_model=ApiKeyResponse)
async def get_api_key(
    current_user: ActiveUser,
):
    """Get current user's API key"""
    if not current_user.api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No API key generated. Please generate one first.",
        )

    # Note: Using updated_at as a proxy for API key creation time.
    # This timestamp reflects the last time the user record was updated,
    # which includes API key generation/regeneration.
    return {"api_key": current_user.api_key, "created_at": current_user.updated_at}


@router.post("/api-key/generate", response_model=ApiKeyResponse)
async def generate_user_api_key(
    api_key_data: ApiKeyGenerate,
    request: Request,
    current_user: ActiveUser,
    db: DatabaseSession,
):
    """Generate a new API key for the current user (or regenerate if exists)"""
    # Validate CAPTCHA if provided (optional for automation)
    if api_key_data.captcha_token and api_key_data.captcha_code:
        is_valid = await captcha_service.validate_captcha(
            api_key_data.captcha_token, api_key_data.captcha_code
        )
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired CAPTCHA code"
            )

    # Generate new API key
    new_api_key = generate_api_key()

    # Check if the generated key already exists (very unlikely but possible)
    max_retries = 5
    for _ in range(max_retries):
        existing_user = await User.get_by_api_key(db, new_api_key)
        if not existing_user:
            break
        new_api_key = generate_api_key()
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate unique API key. Please try again.",
        )

    # Update user's API key
    current_user.api_key = new_api_key
    await db.commit()
    await db.refresh(current_user)
    await record_audit_event(
        category="settings",
        action="api_key.generate",
        status="success",
        user=current_user,
        request=request,
    )

    return {"api_key": current_user.api_key, "created_at": current_user.updated_at}


@router.delete("/api-key")
async def revoke_api_key(request: Request, current_user: ActiveUser, db: DatabaseSession):
    """Revoke (delete) the current user's API key"""
    if not current_user.api_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No API key to revoke")

    # Remove API key
    current_user.api_key = None
    await db.commit()
    await record_audit_event(
        category="settings",
        action="api_key.revoke",
        status="success",
        user=current_user,
        request=request,
    )

    return {"success": True, "message": "API key revoked successfully"}


@router.get("/steam-api-key", response_model=SteamApiKeyResponse)
async def get_steam_api_key(
    current_user: ActiveUser,
):
    """Get current user's Steam API key"""
    return {"steam_api_key": current_user.steam_api_key}


@router.get("/github-token-status", response_model=GitHubTokenStatusResponse)
async def get_github_token_status(
    current_user: ActiveUser,
):
    """Get GitHub token configuration status (without revealing the full token)"""
    has_token = current_user.has_github_token
    token_prefix = None

    if has_token and current_user.github_token:
        # Show only the prefix (first 20 chars) to confirm token is set
        token_prefix = current_user.github_token[:20] + "..."

    return {"has_token": has_token, "token_prefix": token_prefix}


@router.get("/s3-settings", response_model=S3SettingsResponse)
async def get_s3_settings(
    current_user: ActiveUser,
):
    """Get current user's S3 backup settings without revealing the secret key"""
    return _build_s3_settings_response(current_user)


@router.put("/s3-settings", response_model=S3SettingsResponse)
async def update_s3_settings(
    settings_data: S3SettingsUpdate,
    request: Request,
    current_user: ActiveUser,
    db: DatabaseSession,
):
    """Update current user's S3 backup settings"""
    is_valid = await captcha_service.validate_captcha(
        settings_data.captcha_token, settings_data.captcha_code
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired CAPTCHA code"
        )

    if settings_data.enabled is not None:
        current_user.s3_enabled = settings_data.enabled
    if settings_data.endpoint_url is not None:
        current_user.s3_endpoint_url = settings_data.endpoint_url or None
    if settings_data.region is not None:
        current_user.s3_region = settings_data.region or None
    if settings_data.bucket is not None:
        current_user.s3_bucket = settings_data.bucket or None
    if settings_data.access_key_id is not None:
        current_user.s3_access_key_id = settings_data.access_key_id or None
    if settings_data.prefix is not None:
        current_user.s3_prefix = settings_data.prefix or None
    if settings_data.use_ssl is not None:
        current_user.s3_use_ssl = settings_data.use_ssl
    if settings_data.retention_count is not None:
        current_user.s3_retention_count = settings_data.retention_count

    if settings_data.clear_secret:
        current_user.s3_secret_access_key = None
    elif (
        settings_data.secret_access_key is not None
        and settings_data.secret_access_key.strip() != ""
    ):
        current_user.s3_secret_access_key = settings_data.secret_access_key.strip()

    changed = [
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
        if getattr(settings_data, name) is not None
        or (name == "clear_secret" and settings_data.clear_secret)
    ]
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
                "secret_access_key" if field == "secret_access_key" else field
                for field in changed
                if field != "secret_access_key" or settings_data.secret_access_key
            ]
        },
    )

    return _build_s3_settings_response(current_user)


@router.post("/s3-settings/test")
async def test_s3_settings(
    current_user: ActiveUser,
):
    """Test the saved S3 backup settings"""
    success, message, steps = await s3_backup_service.test_connection(current_user)
    return {"success": success, "message": message, "steps": steps}


@router.post("/generate-server-token", response_model=GenerateServerTokenResponse)
async def generate_server_token(
    request_data: GenerateServerTokenRequest,
    current_user: ActiveUser,
    db: DatabaseSession,
):
    """Generate a Steam game server login token (GSLT) using user's Steam API key"""
    # Validate CAPTCHA (required for security)
    is_valid = await captcha_service.validate_captcha(
        request_data.captcha_token, request_data.captcha_code
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired CAPTCHA code"
        )

    # Check if user has Steam API key set
    if not current_user.steam_api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Steam API key not set. Please set your Steam API key in profile settings first.",
        )

    # Use provided server name or fallback to username-based name
    if request_data.server_name and isinstance(request_data.server_name, str):
        memo = request_data.server_name.strip() or f"CS2 Server - {current_user.username}"
    else:
        memo = f"CS2 Server - {current_user.username}"

    success, result = await steam_api_service.create_game_server_account(
        steam_api_key=current_user.steam_api_key, memo=memo
    )

    if not success or result is None or not result.get("success"):
        error_msg = (
            result.get("error", "Unknown error")
            if result is not None
            else "Failed to generate token"
        )
        return GenerateServerTokenResponse(success=False, error=error_msg)

    return GenerateServerTokenResponse(
        success=True, login_token=result.get("login_token") if result is not None else None
    )


@router.post("/forgot-password")
async def forgot_password(
    reset_request: ForgotPasswordRequest, request: Request, db: DatabaseSession
):
    """Request password reset email"""
    from api.password_reset import request_password_reset

    return await request_password_reset(
        email=reset_request.email,
        captcha_token=reset_request.captcha_token,
        captcha_code=reset_request.captcha_code,
        request=request,
        db=db,
    )


@router.post("/reset-password-with-token")
async def reset_password_with_token(
    reset_request: ResetPasswordRequest, request: Request, db: DatabaseSession
):
    """Reset password using reset token"""
    from api.password_reset import complete_password_reset

    return await complete_password_reset(
        token=reset_request.token,
        new_password=reset_request.new_password,
        request=request,
        db=db,
    )


@router.post("/google-oauth", response_model=Token)
async def google_oauth_login(
    oauth_data: GoogleOAuthRequest,
    request: Request,
    response: Response,
    db: DatabaseSession,
):
    """
    Google OAuth login/register endpoint

    If user exists with this Google ID, log them in.
    If user doesn't exist, register a new user with username and password from request.
    Email is auto-bound from Google account.
    """
    logger = logging.getLogger(__name__)
    await enforce_rate_limit(request, "google_oauth", limit=10, window=60)

    try:
        # Verify the Google ID token
        try:
            # Verify with Google Client ID if configured
            client_id = settings.GOOGLE_CLIENT_ID
            if not client_id:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Google OAuth is not configured. Please set GOOGLE_CLIENT_ID in environment variables.",
                )

            idinfo = await to_thread.run_sync(
                id_token.verify_oauth2_token,
                oauth_data.id_token,
                requests.Request(),
                client_id,
            )

            # Get user info from token
            google_user_id = idinfo["sub"]
            email = idinfo.get("email")

            if not email:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email not provided by Google. Please ensure email permission is granted.",
                )

        except ValueError as e:
            logger.error(f"Invalid Google token: {e}")
            await record_audit_event(
                category="auth",
                action="google_oauth",
                status="failure",
                request=request,
                details={"reason": "invalid_token"},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google ID token"
            ) from e

        # Check if user exists with this Google ID
        user = await User.get_by_google_id(db, google_user_id)

        if user:
            # User exists, log them in
            if not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="User account is inactive"
                )

            # Create access token
            access_token_expires = timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
            access_token = create_access_token(
                data={"sub": str(user.id), "username": user.username},
                expires_delta=access_token_expires,
            )
            set_web_session_cookie(request, response, access_token)
            await record_audit_event(
                category="auth",
                action="google_oauth",
                status="success",
                user=user,
                request=request,
                details={"flow": "login"},
            )

            return {"access_token": access_token, "token_type": "bearer"}

        else:
            # User doesn't exist, need to register
            if not oauth_data.username or not oauth_data.password:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Username and password required for new Google account registration",
                )

            # Check if username already exists
            existing_user = await User.get_by_username(db, oauth_data.username)
            if existing_user:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Username already taken. Please choose a different username.",
                )

            # Check if email already exists (from non-Google registration)
            existing_email = await User.get_by_email(db, email)
            if existing_email:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="An account with this email already exists. Please use regular login.",
                )

            await db.commit()
            # Create new user with Google OAuth
            hashed_password = await get_password_hash_async(oauth_data.password)
            new_user = User(
                username=oauth_data.username,
                email=email,
                hashed_password=hashed_password,
                google_id=google_user_id,
                oauth_provider="google",
                is_active=True,
            )
            db.add(new_user)
            await db.commit()
            await db.refresh(new_user)

            # Create access token for the new user
            access_token_expires = timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
            access_token = create_access_token(
                data={"sub": str(new_user.id), "username": new_user.username},
                expires_delta=access_token_expires,
            )
            set_web_session_cookie(request, response, access_token)
            await record_audit_event(
                category="auth",
                action="google_oauth",
                status="success",
                user=new_user,
                request=request,
                details={"flow": "register"},
            )

            return {"access_token": access_token, "token_type": "bearer"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in Google OAuth login: {e}", exc_info=True)
        await record_audit_event(
            category="auth",
            action="google_oauth",
            status="failure",
            request=request,
            details={"reason": "oauth_error"},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Google OAuth login failed: {str(e)}",
        ) from e


@router.post("/logout")
async def logout(request: Request, response: Response, db: DatabaseSession):
    """Clear the HTTP-only browser session cookie."""
    from modules.auth import _get_active_user_for_token, web_session_cookie_name

    token = request.cookies.get(web_session_cookie_name())
    user = await _get_active_user_for_token(token, db) if token else None
    await record_audit_event(
        category="auth",
        action="logout",
        status="success",
        user=user,
        request=request,
    )
    clear_web_session_cookie(response)
    return {"success": True}
