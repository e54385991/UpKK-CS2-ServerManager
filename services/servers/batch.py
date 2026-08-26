"""Bulk authorization snapshot for server batch operations."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from modules.models import Server


async def authorized_server_ids(
    db: AsyncSession,
    requested_ids: list[int],
    owner_user_id: int,
) -> list[int]:
    """Resolve a request with one SQL query while preserving request order."""
    result = await db.execute(
        select(Server.id).where(
            Server.id.in_(requested_ids),
            Server.user_id == owner_user_id,
        )
    )
    allowed = set(result.scalars().all())
    return [server_id for server_id in requested_ids if server_id in allowed]
