"""Versioned passkey registration, management, and passwordless login."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status

from api.contracts.v1.identity import AuthTokenView
from api.contracts.v1.passkeys import (
    PasskeyCredentialRequest,
    PasskeyListView,
    PasskeyLoginOptionsRequest,
    PasskeyOptionsView,
    PasskeyPatchRequest,
    PasskeyView,
)
from api.contracts.v1.settings import ActionResult
from api.dependencies import ActiveUser, DatabaseSession
from modules.auth import create_access_token, set_web_session_cookie
from modules.config import get_settings
from modules.models import User, WebAuthnCredential
from services.audit_log_service import INVALID_CREDENTIALS_DETAILS, record_audit_event
from services.passkeys import (
    PasskeyError,
    complete_authentication,
    complete_registration,
    delete_credential,
    list_passkeys,
    rename_credential,
    start_authentication,
    start_registration,
)
from services.rate_limit import enforce_rate_limit

router = APIRouter(prefix="/api/v1/auth/passkeys", tags=["v1-auth"])


def _http_error(exc: PasskeyError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.code)


def _to_view(row: WebAuthnCredential) -> PasskeyView:
    return PasskeyView(
        id=int(row.id or 0),
        nickname=row.nickname or "",
        transports=list(row.transports or []),
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        backup_eligible=row.backup_eligible,
        backup_state=row.backup_state,
    )


def _issue_session(request: Request, response: Response, user: User) -> AuthTokenView:
    settings = get_settings()
    access_token = create_access_token(
        data={"sub": str(user.id), "username": user.username},
        expires_delta=timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    set_web_session_cookie(request, response, access_token)
    return AuthTokenView(access_token=access_token, token_type="bearer")


@router.get("", response_model=PasskeyListView)
async def list_registered_passkeys(
    current_user: ActiveUser,
    db: DatabaseSession,
) -> PasskeyListView:
    if current_user.id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="passkey_verification_failed"
        )
    rows = await list_passkeys(db, current_user.id)
    return PasskeyListView(items=[_to_view(row) for row in rows])


@router.post("/register/options", response_model=PasskeyOptionsView)
async def register_options(
    request: Request,
    current_user: ActiveUser,
    db: DatabaseSession,
) -> PasskeyOptionsView:
    await enforce_rate_limit(
        request, "passkey_register_options", limit=10, window=60, identity=str(current_user.id)
    )
    try:
        public_key = await start_registration(
            origin_header=request.headers.get("origin"),
            user=current_user,
            db=db,
        )
    except PasskeyError as exc:
        raise _http_error(exc) from exc
    return PasskeyOptionsView(public_key=public_key)


@router.post("/register/verify", response_model=PasskeyView)
async def register_verify(
    body: PasskeyCredentialRequest,
    request: Request,
    current_user: ActiveUser,
    db: DatabaseSession,
) -> PasskeyView:
    await enforce_rate_limit(
        request, "passkey_register_verify", limit=10, window=60, identity=str(current_user.id)
    )
    try:
        row = await complete_registration(
            origin_header=request.headers.get("origin"),
            user=current_user,
            db=db,
            credential=body.credential,
            nickname=body.nickname,
        )
    except PasskeyError as exc:
        await record_audit_event(
            category="auth",
            action="passkey_register",
            status="failure",
            user=current_user,
            request=request,
            details={"reason": exc.code},
        )
        raise _http_error(exc) from exc
    await record_audit_event(
        category="auth",
        action="passkey_register",
        status="success",
        user=current_user,
        request=request,
        details={"credential_pk": row.id},
    )
    return _to_view(row)


@router.patch("/{credential_id}", response_model=PasskeyView)
async def patch_passkey(
    credential_id: int,
    body: PasskeyPatchRequest,
    request: Request,
    current_user: ActiveUser,
    db: DatabaseSession,
) -> PasskeyView:
    await enforce_rate_limit(
        request, "passkey_patch", limit=20, window=60, identity=str(current_user.id)
    )
    if current_user.id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="passkey_verification_failed"
        )
    try:
        row = await rename_credential(db, current_user.id, credential_id, body.nickname)
    except PasskeyError as exc:
        raise _http_error(exc) from exc
    return _to_view(row)


@router.delete("/{credential_id}", response_model=ActionResult)
async def remove_passkey(
    credential_id: int,
    request: Request,
    current_user: ActiveUser,
    db: DatabaseSession,
) -> ActionResult:
    await enforce_rate_limit(
        request, "passkey_delete", limit=20, window=60, identity=str(current_user.id)
    )
    if current_user.id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="passkey_verification_failed"
        )
    try:
        await delete_credential(db, current_user.id, credential_id)
    except PasskeyError as exc:
        await record_audit_event(
            category="auth",
            action="passkey_delete",
            status="failure",
            user=current_user,
            request=request,
            details={"reason": exc.code, "credential_pk": credential_id},
        )
        raise _http_error(exc) from exc
    await record_audit_event(
        category="auth",
        action="passkey_delete",
        status="success",
        user=current_user,
        request=request,
        details={"credential_pk": credential_id},
    )
    return ActionResult(success=True, message="Passkey removed")


@router.post("/login/options", response_model=PasskeyOptionsView)
async def login_options(
    request: Request,
    db: DatabaseSession,
    body: PasskeyLoginOptionsRequest | None = None,
) -> PasskeyOptionsView:
    username = (body.username if body is not None else None) or ""
    await enforce_rate_limit(
        request, "passkey_login_options", limit=10, window=60, identity=username
    )
    try:
        public_key = await start_authentication(
            origin_header=request.headers.get("origin"),
            db=db,
            username=username or None,
        )
    except PasskeyError as exc:
        raise _http_error(exc) from exc
    return PasskeyOptionsView(public_key=public_key)


@router.post("/login/verify", response_model=AuthTokenView)
async def login_verify(
    body: PasskeyCredentialRequest,
    request: Request,
    response: Response,
    db: DatabaseSession,
) -> AuthTokenView:
    await enforce_rate_limit(request, "passkey_login_verify", limit=10, window=60)
    try:
        user = await complete_authentication(
            origin_header=request.headers.get("origin"),
            db=db,
            credential=body.credential,
        )
    except PasskeyError as exc:
        await record_audit_event(
            category="auth",
            action="login",
            status="failure",
            request=request,
            details={**INVALID_CREDENTIALS_DETAILS, "method": "passkey", "reason": exc.code},
        )
        raise _http_error(exc) from exc
    await record_audit_event(
        category="auth",
        action="login",
        status="success",
        user=user,
        request=request,
        details={"method": "passkey"},
    )
    return _issue_session(request, response, user)
