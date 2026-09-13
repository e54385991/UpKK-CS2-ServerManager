"""HTTP adapter around the transport-independent server ownership lookup."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from modules import Server, User
from services.plugin_installation import get_server_for_user as load_server_for_user


async def get_server_for_user(server_id: int, db: AsyncSession, current_user: User) -> Server:
    """Load a server the caller may use, mapping a miss to HTTP 404."""
    try:
        return await load_server_for_user(db, server_id, current_user)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found"
        ) from None
