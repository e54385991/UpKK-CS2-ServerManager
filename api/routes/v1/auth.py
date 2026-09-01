"""Versioned auth endpoints for the console session."""

from fastapi import APIRouter, Request, Response, status

from api.dependencies import ActiveUser, DatabaseSession, SettingsDependency
from api.password_reset import complete_password_reset, request_password_reset
from api.registration import register_user
from api.routes.auth import google_oauth_login
from modules import GoogleOAuthRequest

from .schemas import (
    ActionResult,
    AuthTokenView,
    GoogleConfigView,
    GoogleSignInRequest,
    PasswordResetCompleteRequest,
    PasswordResetEmailRequest,
    RegisterRequest,
    SessionUser,
)

router = APIRouter(prefix="/api/v1/auth", tags=["v1-auth"])


@router.post("/register", response_model=SessionUser, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    request: Request,
    db: DatabaseSession,
) -> SessionUser:
    """Create a console member. Does not start a session; sign in afterwards."""
    user = await register_user(
        username=body.username,
        email=str(body.email),
        password=body.password,
        captcha_token=body.captcha_token,
        captcha_code=body.captcha_code,
        request=request,
        db=db,
    )
    if user.id is None:
        raise RuntimeError("Registered user is missing a primary key")
    return SessionUser(
        id=user.id,
        username=user.username,
        email=user.email,
        is_admin=user.is_admin,
        is_active=user.is_active,
    )


@router.get("/me", response_model=SessionUser)
async def read_current_session(current_user: ActiveUser) -> SessionUser:
    """Return the authenticated principal for the browser session."""
    return SessionUser(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        is_admin=current_user.is_admin,
        is_active=current_user.is_active,
    )


@router.post("/forgot-password", response_model=ActionResult)
async def forgot_password(
    body: PasswordResetEmailRequest,
    request: Request,
    db: DatabaseSession,
) -> ActionResult:
    """Send a password-reset email when the address matches an account."""
    result = await request_password_reset(
        email=body.email,
        captcha_token=body.captcha_token,
        captcha_code=body.captcha_code,
        request=request,
        db=db,
    )
    return ActionResult(success=bool(result["success"]), message=str(result["message"]))


@router.post("/reset-password", response_model=ActionResult)
async def reset_password(
    body: PasswordResetCompleteRequest,
    request: Request,
    db: DatabaseSession,
) -> ActionResult:
    """Set a new password from a one-time reset token."""
    result = await complete_password_reset(
        token=body.token,
        new_password=body.new_password,
        request=request,
        db=db,
    )
    return ActionResult(success=bool(result["success"]), message=str(result["message"]))


@router.get("/google-config", response_model=GoogleConfigView)
async def google_config(app_settings: SettingsDependency) -> GoogleConfigView:
    """Public Google OAuth client id for the Next.js login popup."""
    client_id = (app_settings.GOOGLE_CLIENT_ID or "").strip()
    return GoogleConfigView(client_id=client_id, enabled=bool(client_id))


@router.post("/google-oauth", response_model=AuthTokenView)
async def google_oauth(
    body: GoogleSignInRequest,
    request: Request,
    response: Response,
    db: DatabaseSession,
) -> AuthTokenView:
    """Verify a Google ID token, set the session cookie, and return the same token body as login."""
    result = await google_oauth_login(
        GoogleOAuthRequest(
            id_token=body.id_token,
            username=body.username,
            password=body.password,
        ),
        request,
        response,
        db,
    )
    payload = result if isinstance(result, dict) else result.model_dump()
    return AuthTokenView(
        access_token=str(payload["access_token"]),
        token_type=str(payload.get("token_type") or "bearer"),
    )
