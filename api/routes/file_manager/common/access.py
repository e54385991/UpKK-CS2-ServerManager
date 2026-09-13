"""Server lookup and one-time download ticket helpers."""

from __future__ import annotations

from typing import Optional

from fastapi import Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import DatabaseSession
from modules import Server, User
from services.compat import LateBoundModule

host = LateBoundModule("api.routes.file_manager.common")


async def get_server_for_user(server_id: int, db: AsyncSession, current_user: User) -> Server:
    """Helper to get server and verify ownership - admins can access any server"""
    return await host.require_server_access(db, server_id, current_user)


async def _create_download_ticket(user_id: int, server_id: int, path: str) -> str:
    """Create a short-lived one-time ticket bound to a user, server and path."""
    now = host.time.monotonic()
    async with host.download_tickets_lock:
        expired_tickets = [
            ticket for ticket, info in host.download_tickets.items() if info["expires_at"] <= now
        ]
        for ticket in expired_tickets:
            host.download_tickets.pop(ticket, None)

        ticket = host.uuid.uuid4().hex
        host.download_tickets[ticket] = {
            "user_id": user_id,
            "server_id": server_id,
            "path": path,
            "expires_at": now + host.DOWNLOAD_TICKET_TTL_SECONDS,
        }
        return ticket


async def _consume_download_ticket(ticket: str, server_id: int, path: str) -> Optional[int]:
    """Consume and validate a one-time download ticket."""
    now = host.time.monotonic()
    async with host.download_tickets_lock:
        ticket_info = host.download_tickets.pop(ticket, None)

    if not ticket_info:
        return None
    if ticket_info["expires_at"] <= now:
        return None
    if ticket_info["server_id"] != server_id or ticket_info["path"] != path:
        return None
    return int(ticket_info["user_id"])


async def get_current_active_user_for_download(
    server_id: int,
    path: str,
    ticket: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    *,
    db: DatabaseSession,
) -> User:
    """Authenticate downloads with a one-time ticket or a normal bearer token."""
    credentials_exception = host.HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    user_id: Optional[int] = None

    if ticket:
        user_id = await host._consume_download_ticket(ticket, server_id, path)
        if user_id is None:
            raise credentials_exception
    elif authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise credentials_exception

        try:
            payload = host.jwt.decode(
                token, host.settings.JWT_SECRET_KEY, algorithms=[host.settings.JWT_ALGORITHM]
            )
            user_id_str = payload.get("sub")
            if user_id_str is None:
                raise credentials_exception
            user_id = int(user_id_str)
        except host.InvalidTokenError, ValueError:
            raise credentials_exception from None
    else:
        raise credentials_exception

    result = await db.execute(host.select(host.User).where(host.User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise host.HTTPException(status_code=400, detail="Inactive user")

    return user
