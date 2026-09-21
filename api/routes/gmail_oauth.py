"""
Gmail OAuth2 routes for system settings
"""

import hashlib
import hmac
import json
import logging
import re
import secrets
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from api.dependencies import AdminUser, DatabaseSession
from modules import (
    GmailCredentialsUploadRequest,
    SystemSettings,
    settings,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gmail-oauth", tags=["gmail-oauth"])

_CALLBACK_PATH = "/api/gmail-oauth/callback"
_PUBLIC_ORIGIN_HEADER = "x-upkk-public-origin"
_SETTINGS_RETURN = "/settings"
_VERIFIER_RE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")


def normalize_public_origin(value: str | None) -> str | None:
    """Accept an http(s) origin and reject paths, queries, and credentials."""
    if not value or not value.strip():
        return None
    parsed = urlparse(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return None
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    if parsed.port:
        return f"{parsed.scheme}://{host}:{parsed.port}"
    return f"{parsed.scheme}://{host}"


def gmail_redirect_uri(origin: str) -> str:
    return f"{origin.rstrip('/')}{_CALLBACK_PATH}"


def authorize_origin(request: Request) -> str:
    """Browser origin from the console, otherwise the configured public URL."""
    headers = getattr(request, "headers", None)
    raw = headers.get(_PUBLIC_ORIGIN_HEADER) if headers is not None else None
    return normalize_public_origin(raw) or settings.BACKEND_URL


def generate_code_verifier() -> str:
    """PKCE verifier. Google requires the same value when the code is exchanged."""
    return secrets.token_urlsafe(64)


def sign_oauth_state(origin: str, code_verifier: str) -> str:
    payload = json.dumps({"o": origin, "v": code_verifier}, separators=(",", ":"))
    return f"{_origin_digest(payload)}.{payload}"


def oauth_state_payload(state: str | None) -> dict | None:
    if not state or "." not in state:
        return None
    digest, _, payload = state.partition(".")
    if not digest or not payload.startswith("{"):
        return None
    try:
        matches = hmac.compare_digest(digest, _origin_digest(payload))
    except ValueError:
        return None
    if not matches:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def origin_from_oauth_state(state: str | None) -> str | None:
    data = oauth_state_payload(state)
    if data is None:
        return None
    origin = data.get("o")
    if not isinstance(origin, str):
        return None
    return normalize_public_origin(origin)


def verifier_from_oauth_state(state: str | None) -> str | None:
    data = oauth_state_payload(state)
    if data is None:
        return None
    verifier = data.get("v")
    if not isinstance(verifier, str) or _VERIFIER_RE.fullmatch(verifier) is None:
        return None
    return verifier


def callback_origin(state: str | None) -> str:
    return origin_from_oauth_state(state) or settings.BACKEND_URL


def _origin_digest(origin: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), origin.encode(), hashlib.sha256).hexdigest()


@router.get("/authorize")
async def gmail_oauth_authorize(
    request: Request,
    db: DatabaseSession,
    current_user: AdminUser,
):
    """
    Start Gmail OAuth2 authorization flow (admin only)

    This endpoint redirects the user to Google's OAuth consent screen.
    After authorization, Google will redirect back to /api/gmail-oauth/callback
    """
    try:
        from google_auth_oauthlib.flow import Flow

        # Get system settings to check if credentials are configured
        sys_settings = await SystemSettings.get_or_create_settings(db)

        if not sys_settings.gmail_credentials_json:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Gmail API credentials not configured. Please upload credentials JSON first.",
            )

        # Parse credentials JSON
        try:
            credentials_info = json.loads(sys_settings.gmail_credentials_json)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Gmail credentials JSON format",
            ) from None

        origin = authorize_origin(request)
        code_verifier = generate_code_verifier()
        flow = Flow.from_client_config(
            credentials_info,
            scopes=["https://www.googleapis.com/auth/gmail.send"],
            redirect_uri=gmail_redirect_uri(origin),
            code_verifier=code_verifier,
            autogenerate_code_verifier=False,
        )

        # State carries the signed origin and PKCE verifier. The callback builds
        # a new Flow, so the verifier has to travel with the browser redirect.
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",  # Force consent screen to get refresh token
            state=sign_oauth_state(origin, code_verifier),
        )

        # Store state in session or cache for verification in callback
        # For simplicity, we'll store it in the credentials JSON temporarily
        # In production, use Redis or session storage

        return {"authorization_url": authorization_url, "state": state}

    except ImportError as e:
        logger.error(f"Gmail OAuth libraries not installed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gmail OAuth libraries not installed. Please install google-auth-oauthlib.",
        ) from e
    except Exception as e:
        logger.error(f"Error starting Gmail OAuth flow: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start OAuth flow: {str(e)}",
        ) from e


@router.get("/callback")
async def gmail_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    *,
    db: DatabaseSession,
):
    """
    Handle OAuth2 callback from Google

    This endpoint is called by Google after the user authorizes the application.
    It exchanges the authorization code for access and refresh tokens.
    """
    try:
        # Check for errors from OAuth provider
        if error:
            logger.error(f"OAuth error: {error}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"OAuth authorization failed: {error}",
            )

        if not code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Authorization code not provided"
            )

        from google_auth_oauthlib.flow import Flow

        # Get system settings
        sys_settings = await SystemSettings.get_or_create_settings(db)

        if not sys_settings.gmail_credentials_json:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Gmail API credentials not configured",
            )

        code_verifier = verifier_from_oauth_state(state)
        if code_verifier is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OAuth code verifier is missing",
            )

        # Parse credentials JSON
        credentials_info = json.loads(sys_settings.gmail_credentials_json)

        flow = Flow.from_client_config(
            credentials_info,
            scopes=["https://www.googleapis.com/auth/gmail.send"],
            redirect_uri=gmail_redirect_uri(callback_origin(state)),
            code_verifier=code_verifier,
            autogenerate_code_verifier=False,
        )

        # Exchange authorization code for tokens
        flow.fetch_token(code=code)

        # Get credentials
        credentials = flow.credentials

        # Store token information
        token_data = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": getattr(credentials, "token_uri", None),
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
        }

        # Save token to database
        sys_settings.gmail_token_json = json.dumps(token_data)
        db.add(sys_settings)
        await db.commit()

        logger.info("Gmail OAuth token saved successfully")

        # Redirect to system settings page with success message
        return RedirectResponse(
            url=f"{_SETTINGS_RETURN}?gmail_auth=success", status_code=status.HTTP_302_FOUND
        )

    except Exception as e:
        logger.error(f"Error in Gmail OAuth callback: {e}", exc_info=True)
        # Redirect to system settings with error
        return RedirectResponse(
            url=f"{_SETTINGS_RETURN}?gmail_auth=error", status_code=status.HTTP_302_FOUND
        )


@router.post("/upload-credentials")
async def upload_gmail_credentials(
    request: GmailCredentialsUploadRequest,
    db: DatabaseSession,
    current_user: AdminUser,
):
    """
    Upload Gmail API credentials JSON (admin only)

    Args:
        request: Request body containing the credentials JSON
    """
    try:
        # Validate JSON format
        credentials_data = json.loads(request.credentials_json)

        # Verify it has the expected structure
        if "web" not in credentials_data and "installed" not in credentials_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid credentials JSON format. Please upload the credentials.json file from Google Cloud Console.",
            )

        # Get system settings
        sys_settings = await SystemSettings.get_or_create_settings(db)

        # Save credentials
        sys_settings.gmail_credentials_json = request.credentials_json
        db.add(sys_settings)
        await db.commit()

        return {
            "success": True,
            "message": "Gmail credentials uploaded successfully. You can now authorize the application.",
        }

    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON format"
        ) from None
    except Exception as e:
        logger.error(f"Error uploading Gmail credentials: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload credentials: {str(e)}",
        ) from e


@router.delete("/revoke")
async def revoke_gmail_authorization(db: DatabaseSession, current_user: AdminUser):
    """
    Revoke Gmail API authorization and clear stored tokens (admin only)
    """
    try:
        # Get system settings
        sys_settings = await SystemSettings.get_or_create_settings(db)

        # Clear token
        sys_settings.gmail_token_json = None
        db.add(sys_settings)
        await db.commit()

        return {"success": True, "message": "Gmail authorization revoked successfully"}

    except Exception as e:
        logger.error(f"Error revoking Gmail authorization: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to revoke authorization: {str(e)}",
        ) from e


@router.get("/status")
async def gmail_oauth_status(db: DatabaseSession, current_user: AdminUser):
    """
    Check Gmail OAuth configuration status (admin only)
    """
    try:
        sys_settings = await SystemSettings.get_or_create_settings(db)

        return {
            "credentials_configured": bool(sys_settings.gmail_credentials_json),
            "token_configured": bool(sys_settings.gmail_token_json),
            "ready": bool(sys_settings.gmail_credentials_json and sys_settings.gmail_token_json),
        }

    except Exception as e:
        logger.error(f"Error checking Gmail OAuth status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to check status: {str(e)}",
        ) from e
