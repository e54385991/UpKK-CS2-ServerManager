"""Write and query metadata-only administrator audit events."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import func
from sqlmodel import select

from modules.database import async_session_maker
from modules.models import AuditLog, DiscordOperationRun, User
from modules.schemas import AuditLogListResponse, AuditLogResponse
from modules.utils import get_current_time
from services.ai_security import redact_sensitive_text, sanitize_tool_result

logger = logging.getLogger(__name__)

AUDIT_LOG_RETENTION_DAYS = 30
INVALID_CREDENTIALS_DETAILS = {"reason": "invalid_credentials"}
_USER_AGENT_LIMIT = 500

AUDIT_CATEGORIES = frozenset({"auth", "discord", "server", "settings", "files", "config", "plugin"})
AUDIT_STATUSES = frozenset({"success", "failure", "cancelled", "expired", "requested", "partial"})


def client_ip_address(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    return request.client.host or "unknown"


def client_user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    value = (request.headers.get("user-agent") or "").strip()
    return value[:_USER_AGENT_LIMIT] or None


def _naive_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.replace(tzinfo=None)


def retention_cutoff(*, now: datetime | None = None) -> datetime:
    moment = now or get_current_time()
    return _naive_datetime(moment - timedelta(days=AUDIT_LOG_RETENTION_DAYS))


def _sanitize_details(details: dict[str, Any] | None) -> dict[str, Any]:
    sanitized = sanitize_tool_result(details or {})
    return sanitized if isinstance(sanitized, dict) else {"result": sanitized}


def discord_operation_details(
    item: DiscordOperationRun, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "operation_id": item.id,
        "guild_id": item.guild_id,
        "channel_id": item.channel_id,
        "discord_action": item.action,
        "required_capabilities": list(item.required_capabilities or []),
    }
    if item.action in {"game_console", "change_map"}:
        details["command_present"] = bool((item.arguments or {}).get("command_encrypted"))
    else:
        details["arguments"] = {
            key: value
            for key, value in (item.arguments or {}).items()
            if "encrypt" not in key.casefold()
            and "password" not in key.casefold()
            and "token" not in key.casefold()
            and "secret" not in key.casefold()
        }
    if item.plan_snapshot:
        details["plan_snapshot"] = item.plan_snapshot
    if item.error:
        details["error"] = redact_sensitive_text(item.error, limit=500)
    if item.result:
        details["result_success"] = bool(item.result.get("success", True))
        message = item.result.get("message") or item.result.get("error")
        if message:
            details["result_message"] = redact_sensitive_text(str(message), limit=500)
    if extra:
        details.update(extra)
    return details


async def record_audit_event(
    *,
    category: str,
    action: str,
    status: str,
    actor_user_id: int | None = None,
    actor_username: str | None = None,
    actor_external_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    source: str = "web",
    server_id: int | None = None,
    details: dict[str, Any] | None = None,
    request: Request | None = None,
    user: User | None = None,
) -> None:
    """Persist one audit row. Failures are logged and never raise to callers."""
    try:
        if user is not None:
            actor_user_id = user.id if actor_user_id is None else actor_user_id
            actor_username = actor_username or user.username
        async with async_session_maker() as db:
            db.add(
                AuditLog(
                    category=category,
                    action=action,
                    status=status,
                    actor_user_id=actor_user_id,
                    actor_username=actor_username,
                    actor_external_id=actor_external_id,
                    ip_address=ip_address or client_ip_address(request),
                    user_agent=user_agent or client_user_agent(request),
                    source=source,
                    server_id=server_id,
                    details=_sanitize_details(details),
                )
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to record audit event action=%s", action)


async def record_discord_operation_event(
    item: DiscordOperationRun,
    status: str,
    extra: dict[str, Any] | None = None,
) -> None:
    await record_audit_event(
        category="discord",
        action=f"discord.{item.action}",
        status=status,
        actor_user_id=item.owner_user_id,
        actor_external_id=item.actor_user_id,
        source="discord",
        server_id=item.server_id,
        details=discord_operation_details(item, extra),
    )


async def list_audit_logs(
    db,
    *,
    category: str | None = None,
    status: str | None = None,
    username: str | None = None,
    ip_address: str | None = None,
    server_id: int | None = None,
    action: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> AuditLogListResponse:
    cutoff = retention_cutoff()
    filters = [AuditLog.created_at >= cutoff]
    if category:
        filters.append(AuditLog.category == category)
    if status:
        filters.append(AuditLog.status == status)
    if username:
        filters.append(AuditLog.actor_username.ilike(f"%{username.strip()}%"))
    if ip_address:
        filters.append(AuditLog.ip_address == ip_address.strip())
    if server_id is not None:
        filters.append(AuditLog.server_id == server_id)
    if action:
        filters.append(AuditLog.action == action)

    total = await db.scalar(select(func.count()).select_from(AuditLog).where(*filters))
    result = await db.execute(
        select(AuditLog)
        .where(*filters)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset(offset)
        .limit(limit)
    )
    items = [
        AuditLogResponse(
            id=item.id,
            created_at=item.created_at,
            category=item.category,
            action=item.action,
            status=item.status,
            actor_user_id=item.actor_user_id,
            actor_username=item.actor_username,
            actor_external_id=item.actor_external_id,
            ip_address=item.ip_address,
            user_agent=item.user_agent,
            source=item.source,
            server_id=item.server_id,
            details=item.details or {},
        )
        for item in result.scalars().all()
    ]
    return AuditLogListResponse(
        items=items,
        total=int(total or 0),
        limit=limit,
        offset=offset,
        retention_days=AUDIT_LOG_RETENTION_DAYS,
    )
