"""Shared queue primitives for operation workers."""

from __future__ import annotations

import logging

from modules import User
from modules.database import async_session_maker
from services.audit_log_service import record_audit_event
from services.server_operation_hub import server_operation_hub

logger = logging.getLogger(__name__)


async def _dispatch(record: dict, factory) -> dict:
    """Start now if this job is current; otherwise the hub runs it later."""
    await server_operation_hub.schedule(str(record["operation_id"]), factory)
    return record


def _progress_emitter(operation_id: str):
    async def progress(message: str, _kind: str = "status") -> None:
        await server_operation_hub.emit(
            operation_id,
            "progress",
            kind="output",
            message=message,
        )

    return progress


async def _audit_terminal(
    record: dict | None,
    *,
    category: str,
    action: str,
    success: bool,
    message: str,
    extra: dict | None = None,
) -> None:
    if record is None:
        return
    actor_user_id = int(record["actor_user_id"])
    username = None
    async with async_session_maker() as db:
        user = await db.get(User, actor_user_id)
        if user is not None:
            username = user.username
    details = {"operation_id": str(record["operation_id"]), "message": message}
    if extra:
        details.update(extra)
    await record_audit_event(
        category=category,
        action=action,
        status="success" if success else "failure",
        actor_user_id=actor_user_id,
        actor_username=username,
        server_id=int(record["server_id"]),
        details=details,
    )
