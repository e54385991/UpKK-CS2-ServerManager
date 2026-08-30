"""Versioned S3 plugin-backup list and restore for the Next console."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from api.dependencies import ActiveUser, DatabaseSession, require_server_access
from api.routes.servers.common import get_server_owner_user
from modules import User
from services.s3_backup_service import s3_backup_service
from services.server_operation_hub import ServerOperationConflict

from .operation_locks import reject_stuck_lock_unless_active
from .operation_runner import enqueue_s3_restore
from .operations import to_view
from .schemas import S3BackupItemView, S3BackupListView, S3RestoreBody, ServerOperationView

router = APIRouter(
    prefix="/api/v1/servers/{server_id}/s3-backups",
    tags=["v1-s3-backups"],
)


def _to_item(raw: dict) -> S3BackupItemView:
    return S3BackupItemView(
        key=str(raw.get("key") or ""),
        filename=str(raw.get("filename") or ""),
        size=int(raw.get("size") or 0),
        last_modified=raw.get("last_modified"),
    )


async def _owner_for_backups(db, server, current_user: User):
    return await get_server_owner_user(db, server, current_user)


@router.get("", response_model=S3BackupListView)
async def list_server_s3_backups(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> S3BackupListView:
    """List this server's S3 plugin backups. Never returns S3 credentials."""
    server = await require_server_access(db, server_id, current_user)
    owner = await _owner_for_backups(db, server, current_user)
    configured = s3_backup_service.is_configured(owner)
    success, backups, error = await s3_backup_service.list_backups(owner, server)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
    return S3BackupListView(
        configured=configured,
        items=[_to_item(item) for item in backups],
        message=error or None,
    )


@router.post(
    "/restore",
    response_model=ServerOperationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def restore_server_s3_backup(
    server_id: int,
    body: S3RestoreBody,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    """Restore a listed backup: S3 download, safety snapshot, upload, extract."""
    server = await require_server_access(db, server_id, current_user)
    owner = await _owner_for_backups(db, server, current_user)
    if not s3_backup_service.is_configured(owner):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="S3-compatible storage is not configured.",
        )
    if not s3_backup_service.validate_object_key(owner, server, body.object_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Selected S3 backup does not belong to this server",
        )

    await reject_stuck_lock_unless_active(server_id)

    try:
        record = await enqueue_s3_restore(
            server_id=server_id,
            object_key=body.object_key,
            actor_user_id=current_user.id,
        )
    except ServerOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return to_view(record)
