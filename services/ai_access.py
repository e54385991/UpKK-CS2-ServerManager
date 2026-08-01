"""Shared authorization, rate limiting, and security-audit helpers for AI agents."""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from modules.models import Server, User
from services.ai_security import redact_sensitive_text
from services.redis_manager import redis_manager

logger = logging.getLogger(__name__)


class AgentAccessDenied(PermissionError):
    """Raised when a principal loses access during an agent operation."""


async def authorized_server(db: AsyncSession, user: User, server_id: int) -> Server:
    """Resolve a server through the current principal on every privileged step."""
    current = await db.get(User, user.id)
    if current is None or not current.is_active:
        audit_security_event("inactive_principal", user_id=user.id, server_id=server_id)
        raise AgentAccessDenied("The current user is no longer active")
    server = (
        await Server.get_by_id(db, server_id)
        if current.is_admin
        else await Server.get_by_id_and_user(db, server_id, current.id)
    )
    if server is None:
        audit_security_event("server_access_denied", user_id=current.id, server_id=server_id)
        raise AgentAccessDenied("The selected server is no longer available to this user")
    return server


async def enforce_agent_rate_limit(
    user_id: int,
    operation: str,
    *,
    limit: int,
    window_seconds: int = 60,
) -> None:
    allowed, _retry_after = await redis_manager.hit_rate_limit(
        f"agent_rate:{operation}:{user_id}", limit, window_seconds
    )
    if not allowed:
        audit_security_event("rate_limit", user_id=user_id, operation=operation)
        raise AgentAccessDenied("Too many agent requests; try again shortly")


def audit_security_event(
    event: str,
    *,
    user_id: Optional[int] = None,
    server_id: Optional[int] = None,
    operation: Optional[str] = None,
    detail: Optional[str] = None,
) -> None:
    """Write a deliberately metadata-only security event to the application log."""
    logger.warning(
        "agent_security event=%s user_id=%s server_id=%s operation=%s detail=%s",
        event,
        user_id,
        server_id,
        operation,
        redact_sensitive_text(detail or "", limit=500),
    )
