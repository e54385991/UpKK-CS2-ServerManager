"""Versioned auth endpoints for the console session."""

from fastapi import APIRouter

from api.dependencies import ActiveUser

from .schemas import SessionUser

router = APIRouter(prefix="/api/v1/auth", tags=["v1-auth"])


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
