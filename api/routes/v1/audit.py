"""Versioned admin audit-log listing."""

from fastapi import APIRouter, HTTPException, Query, status

from api.dependencies import AdminUser, DatabaseSession
from services.audit_log_service import (
    AUDIT_CATEGORIES,
    AUDIT_SEARCH_MAX_LENGTH,
    AUDIT_STATUSES,
    list_audit_logs,
)

from .schemas import AuditEntry, AuditListView

router = APIRouter(prefix="/api/v1/audit", tags=["v1-audit"])


@router.get("", response_model=AuditListView)
async def list_audit(
    db: DatabaseSession,
    current_user: AdminUser,
    category: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    username: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=AUDIT_SEARCH_MAX_LENGTH),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AuditListView:
    """List administrator audit events (admin only), paginated and searchable."""
    if category and category not in AUDIT_CATEGORIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid category")
    if status_filter and status_filter not in AUDIT_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid status")

    result = await list_audit_logs(
        db,
        category=category,
        status=status_filter,
        username=username,
        q=q,
        limit=limit,
        offset=offset,
    )
    items = [
        AuditEntry(
            id=item.id,
            created_at=item.created_at,
            category=item.category,
            action=item.action,
            status=item.status,
            actor_username=item.actor_username,
            ip_address=item.ip_address,
            source=item.source,
            server_id=item.server_id,
            details=item.details,
        )
        for item in result.items
    ]
    return AuditListView(
        items=items,
        total=result.total,
        limit=result.limit,
        offset=result.offset,
        retention_days=result.retention_days,
    )
