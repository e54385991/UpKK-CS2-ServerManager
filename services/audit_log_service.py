"""Write and query metadata-only administrator audit events."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import String, cast, func, or_
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col, select

from modules.database import async_session_maker
from modules.models import AuditLog, DiscordOperationRun, SystemSettings, User
from modules.schemas import AuditLogListResponse, AuditLogResponse
from modules.utils import get_current_time
from services.ai_security import redact_sensitive_text, sanitize_tool_result
from services.client_ip import (
    cached_client_ip_header,
    request_client_ip,
    resolve_client_ip,
)

logger = logging.getLogger(__name__)

AUDIT_LOG_RETENTION_DAYS = 30
MIN_AUDIT_LOG_RETENTION_DAYS = 1
MAX_AUDIT_LOG_RETENTION_DAYS = 365
AUDIT_SEARCH_MAX_LENGTH = 100
INVALID_CREDENTIALS_DETAILS = {"reason": "invalid_credentials"}
_USER_AGENT_LIMIT = 500

AUDIT_CATEGORIES = frozenset({"auth", "discord", "server", "settings", "files", "config", "plugin"})
AUDIT_STATUSES = frozenset({"success", "failure", "cancelled", "expired", "requested", "partial"})


def client_ip_address(request: Request | None) -> str | None:
    """Attribute a request without a database round-trip (last known policy)."""
    return resolve_client_ip(request, cached_client_ip_header())


def client_user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    value = (request.headers.get("user-agent") or "").strip()
    return value[:_USER_AGENT_LIMIT] or None


def _naive_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.replace(tzinfo=None)


def clamp_audit_log_retention_days(value: int | None) -> int:
    if value is None:
        return AUDIT_LOG_RETENTION_DAYS
    return max(MIN_AUDIT_LOG_RETENTION_DAYS, min(MAX_AUDIT_LOG_RETENTION_DAYS, int(value)))


def retention_cutoff(*, now: datetime | None = None, days: int | None = None) -> datetime:
    moment = now or get_current_time()
    retained_days = clamp_audit_log_retention_days(days)
    return _naive_datetime(moment - timedelta(days=retained_days))


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def audit_search_clause(term: str) -> ColumnElement[bool]:
    pattern = f"%{_escape_like(term)}%"
    matchers: list[ColumnElement[bool]] = [
        col(AuditLog.actor_username).ilike(pattern, escape="\\"),
        col(AuditLog.action).ilike(pattern, escape="\\"),
        col(AuditLog.ip_address).ilike(pattern, escape="\\"),
        col(AuditLog.source).ilike(pattern, escape="\\"),
        col(AuditLog.actor_external_id).ilike(pattern, escape="\\"),
        col(AuditLog.user_agent).ilike(pattern, escape="\\"),
        col(AuditLog.category).ilike(pattern, escape="\\"),
        col(AuditLog.status).ilike(pattern, escape="\\"),
        cast(col(AuditLog.details), String).ilike(pattern, escape="\\"),
    ]
    if term.isdigit() and 1 <= len(term) <= 10:
        matchers.append(col(AuditLog.server_id) == int(term))
    return or_(*matchers)


async def configured_retention_days(db) -> int:
    value = await db.scalar(select(SystemSettings.audit_log_retention_days).limit(1))
    return clamp_audit_log_retention_days(value)


def _audit_list_filters(
    *,
    cutoff: datetime,
    category: str | None,
    status: str | None,
    username: str | None,
    ip_address: str | None,
    server_id: int | None,
    action: str | None,
    q: str | None,
) -> list[Any]:
    filters = [col(AuditLog.created_at) >= cutoff]
    if category:
        filters.append(col(AuditLog.category) == category)
    if status:
        filters.append(col(AuditLog.status) == status)
    if username:
        name = username.strip()
        if name:
            pattern = f"%{_escape_like(name)}%"
            filters.append(col(AuditLog.actor_username).ilike(pattern, escape="\\"))
    if ip_address:
        filters.append(col(AuditLog.ip_address) == ip_address.strip())
    if server_id is not None:
        filters.append(col(AuditLog.server_id) == server_id)
    if action:
        filters.append(col(AuditLog.action) == action)
    if q:
        term = q.strip()[:AUDIT_SEARCH_MAX_LENGTH]
        if term:
            filters.append(audit_search_clause(term))
    return filters


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
            if not ip_address:
                ip_address = await request_client_ip(request, db)
            db.add(
                AuditLog(
                    category=category,
                    action=action,
                    status=status,
                    actor_user_id=actor_user_id,
                    actor_username=actor_username,
                    actor_external_id=actor_external_id,
                    ip_address=ip_address,
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
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> AuditLogListResponse:
    days = await configured_retention_days(db)
    cutoff = retention_cutoff(days=days)
    filters = _audit_list_filters(
        cutoff=cutoff,
        category=category,
        status=status,
        username=username,
        ip_address=ip_address,
        server_id=server_id,
        action=action,
        q=q,
    )

    total = await db.scalar(select(func.count()).select_from(AuditLog).where(*filters))
    result = await db.execute(
        select(AuditLog)
        .where(*filters)
        .order_by(col(AuditLog.created_at).desc(), col(AuditLog.id).desc())
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
        retention_days=days,
    )
