"""Versioned game-directory cleanup scan and delete for the Next console."""

from __future__ import annotations

from fastapi import APIRouter

from api.dependencies import ActiveUser, DatabaseSession
from api.routes.servers import maintenance as legacy
from modules import CleanupDeleteRequest as LegacyCleanupDeleteRequest

from .schemas import (
    CleanupDeleteBody,
    CleanupDeleteView,
    CleanupFailedItemView,
    CleanupItemView,
    CleanupScanView,
    CleanupWorkshopView,
)

router = APIRouter(prefix="/api/v1/servers", tags=["v1-cleanup"])


def _item(raw) -> CleanupItemView:
    if isinstance(raw, dict):
        return CleanupItemView(
            path=str(raw.get("path") or ""),
            name=str(raw.get("name") or ""),
            type=str(raw.get("type") or ""),
            size=int(raw.get("size") or 0),
            category=str(raw.get("category") or ""),
            reason=str(raw.get("reason") or ""),
            danger_level=str(raw.get("danger_level") or ""),
        )
    return CleanupItemView(
        path=str(getattr(raw, "path", "") or ""),
        name=str(getattr(raw, "name", "") or ""),
        type=str(getattr(raw, "type", "") or ""),
        size=int(getattr(raw, "size", 0) or 0),
        category=str(getattr(raw, "category", "") or ""),
        reason=str(getattr(raw, "reason", "") or ""),
        danger_level=str(getattr(raw, "danger_level", "") or ""),
    )


def _scan_view(payload) -> CleanupScanView:
    if isinstance(payload, dict):
        workshop = payload.get("workshop_summary") or {}
        return CleanupScanView(
            safe_items=[_item(item) for item in payload.get("safe_items") or []],
            archive_items=[_item(item) for item in payload.get("archive_items") or []],
            workshop_summary=CleanupWorkshopView(
                path=str(workshop.get("path") or ""),
                item_count=int(workshop.get("item_count") or 0),
                size=int(workshop.get("size") or 0),
            ),
            total_size=int(payload.get("total_size") or 0),
        )
    workshop = getattr(payload, "workshop_summary", None)
    return CleanupScanView(
        safe_items=[_item(item) for item in getattr(payload, "safe_items", []) or []],
        archive_items=[_item(item) for item in getattr(payload, "archive_items", []) or []],
        workshop_summary=CleanupWorkshopView(
            path=str(getattr(workshop, "path", "") or ""),
            item_count=int(getattr(workshop, "item_count", 0) or 0),
            size=int(getattr(workshop, "size", 0) or 0),
        ),
        total_size=int(getattr(payload, "total_size", 0) or 0),
    )


def _delete_view(payload) -> CleanupDeleteView:
    if isinstance(payload, dict):
        failed = payload.get("failed_items") or []
        return CleanupDeleteView(
            success=bool(payload.get("success")),
            message=str(payload.get("message") or ""),
            deleted_count=int(payload.get("deleted_count") or 0),
            freed_bytes_estimate=int(payload.get("freed_bytes_estimate") or 0),
            failed_items=[
                CleanupFailedItemView(
                    path=str(
                        item.get("path") if isinstance(item, dict) else getattr(item, "path", "")
                    ),
                    error=str(
                        item.get("error") if isinstance(item, dict) else getattr(item, "error", "")
                    ),
                )
                for item in failed
            ],
        )
    failed = getattr(payload, "failed_items", []) or []
    return CleanupDeleteView(
        success=bool(getattr(payload, "success", False)),
        message=str(getattr(payload, "message", "") or ""),
        deleted_count=int(getattr(payload, "deleted_count", 0) or 0),
        freed_bytes_estimate=int(getattr(payload, "freed_bytes_estimate", 0) or 0),
        failed_items=[
            CleanupFailedItemView(
                path=str(item.get("path") if isinstance(item, dict) else getattr(item, "path", "")),
                error=str(
                    item.get("error") if isinstance(item, dict) else getattr(item, "error", "")
                ),
            )
            for item in failed
        ],
    )


@router.get("/{server_id}/cleanup/scan", response_model=CleanupScanView)
async def scan_server_cleanup(
    server_id: int, db: DatabaseSession, current_user: ActiveUser
) -> CleanupScanView:
    return _scan_view(await legacy.scan_server_cleanup(server_id, db, current_user))


@router.post("/{server_id}/cleanup/delete", response_model=CleanupDeleteView)
async def delete_server_cleanup_items(
    server_id: int,
    body: CleanupDeleteBody,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> CleanupDeleteView:
    return _delete_view(
        await legacy.delete_server_cleanup_items(
            server_id,
            LegacyCleanupDeleteRequest(
                mode=body.mode,
                paths=body.paths,
                confirmation_text=body.confirmation_text,
            ),
            db,
            current_user,
        )
    )
