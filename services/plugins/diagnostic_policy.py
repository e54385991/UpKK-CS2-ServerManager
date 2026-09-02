"""Shared diagnostic coordination state for background automation."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from modules.models import PluginDiagnosticRun

ACTIVE_DIAGNOSTIC_STATUSES = (
    "running",
    "interrupted",
    "failed",
    "inconclusive",
    "completed_with_quarantine",
)

# This process-local cache is only a fast path. Persistent diagnostic state is
# authoritative whenever a database session is available.
blocked_servers: set[int] = set()


async def has_diagnostic_blocker(server_id: int, db: AsyncSession | None = None) -> bool:
    """Return whether background automation must leave the server untouched."""
    if server_id in blocked_servers:
        return True
    if db is None:
        return False

    result = await db.execute(
        select(PluginDiagnosticRun.id).where(
            PluginDiagnosticRun.server_id == server_id,
            col(PluginDiagnosticRun.status).in_(ACTIVE_DIAGNOSTIC_STATUSES),
        )
    )
    if result is None or not hasattr(result, "first"):
        return False
    blocked = result.first() is not None
    if blocked:
        blocked_servers.add(server_id)
    return blocked
