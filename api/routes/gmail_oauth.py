"""
Gmail OAuth2 routes for system settings
"""

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any, Optional, Protocol
from typing import cast as type_cast

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import LargeBinary, cast, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_admin_principal, get_unit_of_work
from cs2_manager.core import ErrorResponse, Principal
from cs2_manager.infrastructure import UnitOfWork
from cs2_manager.infrastructure.credentials import credential_shadow_update_values
from modules import (
    GmailCredentialsUploadRequest,
    SystemSettings,
    User,
    get_current_admin_user,
    get_db,
)
from modules.schemas.system import GmailOAuthActionResponse, GmailOAuthStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gmail-oauth", tags=["gmail-oauth"])

GMAIL_OAUTH_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_OAUTH_STATE_TTL_SECONDS = 600
GMAIL_OAUTH_STATE_PREFIX = "oauth:gmail:state:"


def _uow_session(uow: UnitOfWork) -> AsyncSession:
    if uow.session is None:
        raise RuntimeError("Unit of work is not active")
    return uow.session


async def _get_or_create_system_settings(db: AsyncSession) -> SystemSettings:
    settings = await SystemSettings.get_settings(db)
    if settings is None:
        settings = SystemSettings()
        db.add(settings)
    return settings


class OAuthStateStoreUnavailable(RuntimeError):
    """Raised when OAuth coordination cannot be performed safely."""


class OAuthStateRedisClient(Protocol):
    """Atomic Redis operations required by the OAuth state store."""

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int,
        nx: bool,
    ) -> object: ...

    async def eval(
        self,
        script: str,
        key_count: int,
        key: str,
    ) -> object: ...


class OAuthStateRedisAdapter(Protocol):
    client: OAuthStateRedisClient


def _oauth_state_redis(request: Request) -> OAuthStateRedisAdapter:
    """Resolve only the Redis adapter owned by the current application."""
    try:
        container = request.app.state.container
        adapter = container.redis
        client = adapter.client
    except (AttributeError, KeyError) as exc:
        raise OAuthStateStoreUnavailable("OAuth state storage is unavailable") from exc

    if not callable(getattr(client, "set", None)) or not callable(getattr(client, "eval", None)):
        raise OAuthStateStoreUnavailable("OAuth state storage is unavailable")
    return type_cast(OAuthStateRedisAdapter, adapter)


def _oauth_redirect_uri(request: Request) -> str:
    """Build the callback URL from the current application's settings."""
    try:
        backend_url = request.app.state.settings.BACKEND_URL
    except (AttributeError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OAuth application settings are unavailable",
        ) from exc
    return f"{str(backend_url).rstrip('/')}/api/gmail-oauth/callback"


def _authorization_context_fingerprint(credentials_json: str, token_json: Optional[str]) -> str:
    """Bind a flow to the exact Gmail configuration it was started from."""
    payload = json.dumps(
        [credentials_json, token_json], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _state_key(state: str) -> str:
    return f"{GMAIL_OAUTH_STATE_PREFIX}{state}"


async def _store_oauth_state(
    redis_adapter: OAuthStateRedisAdapter,
    state: str,
    payload: dict[str, Any],
) -> None:
    """Store an OAuth transaction with a short TTL and collision protection."""
    try:
        stored = await asyncio.wait_for(
            redis_adapter.client.set(
                _state_key(state),
                json.dumps(payload, separators=(",", ":")),
                ex=GMAIL_OAUTH_STATE_TTL_SECONDS,
                nx=True,
            ),
            timeout=0.75,
        )
    except Exception as exc:
        logger.error("Unable to store Gmail OAuth state: %s", exc)
        raise OAuthStateStoreUnavailable("OAuth state storage is unavailable") from exc
    if not stored:
        raise OAuthStateStoreUnavailable("Unable to reserve a unique OAuth state")


async def _consume_oauth_state(
    redis_adapter: OAuthStateRedisAdapter,
    state: str,
) -> Optional[dict]:
    """Atomically read and delete one OAuth transaction."""
    script = """
    local value = redis.call('get', KEYS[1])
    if value then redis.call('del', KEYS[1]) end
    return value
    """
    try:
        raw_payload = await asyncio.wait_for(
            redis_adapter.client.eval(script, 1, _state_key(state)),
            timeout=0.75,
        )
    except Exception as exc:
        logger.error("Unable to consume Gmail OAuth state: %s", exc)
        raise OAuthStateStoreUnavailable("OAuth state storage is unavailable") from exc

    if raw_payload is None:
        return None
    if isinstance(raw_payload, bytes):
        raw_payload = raw_payload.decode("utf-8")
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError, TypeError:
        logger.warning("Discarded malformed Gmail OAuth state payload")
        return None
    return payload if isinstance(payload, dict) else None


def _oauth_redirect(result: str) -> RedirectResponse:
    response = RedirectResponse(
        url=f"/system-settings?gmail_auth={result}",
        status_code=status.HTTP_302_FOUND,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/authorize")
async def gmail_oauth_authorize(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
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

        # Create OAuth flow
        flow = Flow.from_client_config(
            credentials_info,
            scopes=[GMAIL_OAUTH_SCOPE],
            redirect_uri=_oauth_redirect_uri(request),
        )

        # Generate authorization URL
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",  # Force consent screen to get refresh token
        )

        code_verifier = flow.code_verifier
        if not state or not code_verifier or current_user.id is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="OAuth provider did not create a secure authorization transaction",
            )

        # Release the database transaction before coordinating through Redis.
        context_fingerprint = _authorization_context_fingerprint(
            sys_settings.gmail_credentials_json,
            sys_settings.gmail_token_json,
        )
        await db.commit()
        await _store_oauth_state(
            _oauth_state_redis(request),
            state,
            {
                "version": 1,
                "admin_user_id": current_user.id,
                "code_verifier": code_verifier,
                "context_fingerprint": context_fingerprint,
            },
        )

        response.headers["Cache-Control"] = "no-store"
        return {"authorization_url": authorization_url, "state": state}

    except HTTPException:
        raise
    except OAuthStateStoreUnavailable as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OAuth authorization is temporarily unavailable",
        ) from e
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
    db: AsyncSession = Depends(get_db),
):
    """
    Handle OAuth2 callback from Google

    This endpoint is called by Google after the user authorizes the application.
    It exchanges the authorization code for access and refresh tokens.
    """
    if not state or len(state) > 512:
        logger.warning("Rejected Gmail OAuth callback without a valid state parameter")
        return _oauth_redirect("error")

    try:
        oauth_transaction = await _consume_oauth_state(
            _oauth_state_redis(request),
            state,
        )
    except OAuthStateStoreUnavailable as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OAuth callback verification is temporarily unavailable",
        ) from e

    if oauth_transaction is None:
        logger.warning("Rejected missing, expired, or replayed Gmail OAuth state")
        return _oauth_redirect("error")

    try:
        if error or not code:
            logger.warning("Gmail OAuth provider returned an authorization error")
            return _oauth_redirect("error")

        from google_auth_oauthlib.flow import Flow

        admin_user_id = int(oauth_transaction["admin_user_id"])
        code_verifier = oauth_transaction["code_verifier"]
        context_fingerprint = oauth_transaction["context_fingerprint"]
        if not isinstance(code_verifier, str) or not isinstance(context_fingerprint, str):
            raise ValueError("Invalid OAuth transaction")

        initiating_admin = await db.get(User, admin_user_id)
        if not initiating_admin or not initiating_admin.is_active or not initiating_admin.is_admin:
            logger.warning("Rejected Gmail OAuth callback for a non-admin initiator")
            return _oauth_redirect("error")

        sys_settings = await SystemSettings.get_settings(db)
        if not sys_settings or not sys_settings.gmail_credentials_json or sys_settings.id is None:
            return _oauth_redirect("error")

        expected_credentials_json = sys_settings.gmail_credentials_json
        expected_token_json = sys_settings.gmail_token_json
        current_fingerprint = _authorization_context_fingerprint(
            expected_credentials_json,
            expected_token_json,
        )
        if not hmac.compare_digest(context_fingerprint, current_fingerprint):
            logger.warning("Rejected stale Gmail OAuth callback after settings changed")
            return _oauth_redirect("error")

        # Parse credentials JSON
        credentials_info = json.loads(expected_credentials_json)
        settings_id = sys_settings.id
        raw_result = await db.execute(
            select(
                cast(SystemSettings.gmail_credentials_json, LargeBinary),
                cast(SystemSettings.gmail_token_json, LargeBinary),
            ).where(SystemSettings.id == settings_id)
        )
        raw_settings = raw_result.one_or_none()
        if raw_settings is None:
            return _oauth_redirect("error")
        expected_credentials_storage, expected_token_storage = raw_settings
        await db.commit()

        # Create OAuth flow
        flow = Flow.from_client_config(
            credentials_info,
            scopes=[GMAIL_OAUTH_SCOPE],
            state=state,
            code_verifier=code_verifier,
            autogenerate_code_verifier=False,
            redirect_uri=_oauth_redirect_uri(request),
        )

        # Exchange authorization code without blocking the event loop.
        await to_thread.run_sync(lambda: flow.fetch_token(code=code))

        # Get credentials
        credentials = flow.credentials

        # Store token information
        token_data = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
        }

        token_json = json.dumps(token_data)
        conditions = [
            SystemSettings.id == settings_id,
            cast(SystemSettings.gmail_credentials_json, LargeBinary)
            == expected_credentials_storage,
        ]
        if expected_token_storage is None:
            conditions.append(cast(SystemSettings.gmail_token_json, LargeBinary).is_(None))
        else:
            conditions.append(
                cast(SystemSettings.gmail_token_json, LargeBinary) == expected_token_storage
            )
        result = await db.execute(
            update(SystemSettings)
            .where(*conditions)
            .values(
                **credential_shadow_update_values(
                    table_name="system_settings",
                    record_id=settings_id,
                    values={"gmail_token_json": token_json},
                )
            )
        )
        if result.rowcount != 1:
            await db.rollback()
            logger.warning("Rejected Gmail OAuth callback superseded by another settings change")
            return _oauth_redirect("error")
        await db.commit()

        logger.info("Gmail OAuth token saved successfully")

        # Redirect to system settings page with success message
        return _oauth_redirect("success")

    except Exception as e:
        logger.error(f"Error in Gmail OAuth callback: {e}", exc_info=True)
        await db.rollback()
        return _oauth_redirect("error")


@router.post(
    "/upload-credentials",
    response_model=GmailOAuthActionResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def upload_gmail_credentials(
    request: GmailCredentialsUploadRequest,
    uow: UnitOfWork = Depends(get_unit_of_work),
    current_user: Principal = Depends(get_admin_principal),
) -> GmailOAuthActionResponse:
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
        db = _uow_session(uow)
        sys_settings = await _get_or_create_system_settings(db)

        # Save credentials
        sys_settings.gmail_credentials_json = request.credentials_json
        db.add(sys_settings)
        await uow.commit()

        return GmailOAuthActionResponse(
            success=True,
            message="Gmail credentials uploaded successfully. You can now authorize the application.",
        )

    except HTTPException:
        raise
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


@router.delete(
    "/revoke",
    response_model=GmailOAuthActionResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def revoke_gmail_authorization(
    uow: UnitOfWork = Depends(get_unit_of_work),
    current_user: Principal = Depends(get_admin_principal),
) -> GmailOAuthActionResponse:
    """
    Revoke Gmail API authorization and clear stored tokens (admin only)
    """
    try:
        # Get system settings
        db = _uow_session(uow)
        sys_settings = await _get_or_create_system_settings(db)

        # Clear token
        sys_settings.gmail_token_json = None
        db.add(sys_settings)
        await uow.commit()

        return GmailOAuthActionResponse(
            success=True,
            message="Gmail authorization revoked successfully",
        )

    except Exception as e:
        logger.error(f"Error revoking Gmail authorization: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to revoke authorization: {str(e)}",
        ) from e


@router.get(
    "/status",
    response_model=GmailOAuthStatusResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def gmail_oauth_status(
    uow: UnitOfWork = Depends(get_unit_of_work),
    current_user: Principal = Depends(get_admin_principal),
) -> GmailOAuthStatusResponse:
    """
    Check Gmail OAuth configuration status (admin only)
    """
    try:
        db = _uow_session(uow)
        sys_settings = await _get_or_create_system_settings(db)
        await uow.commit()

        return GmailOAuthStatusResponse(
            credentials_configured=bool(sys_settings.gmail_credentials_json),
            token_configured=bool(sys_settings.gmail_token_json),
            ready=bool(sys_settings.gmail_credentials_json and sys_settings.gmail_token_json),
        )

    except Exception as e:
        logger.error(f"Error checking Gmail OAuth status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to check status: {str(e)}",
        ) from e
