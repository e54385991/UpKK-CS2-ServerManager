"""Versioned game-directory cleanup scan and delete for the Next console."""

from __future__ import annotations

import json
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlmodel import col, select

from api.dependencies import ActiveUser, DatabaseSession, StreamUser
from api.routes.servers import maintenance as legacy
from api.routes.servers.common import get_server_with_permission
from api.routes.v1.operation_locks import reject_stuck_lock_unless_active
from api.routes.v1.operation_runner import enqueue_cleanup_delete, enqueue_cleanup_system
from api.routes.v1.operations import to_view
from modules.database import async_session_maker
from modules.models import ScheduledTask
from modules.utils import get_current_time
from services.audit_log_service import record_audit_event
from services.game_cleanup_service import game_cleanup_service
from services.server_operation_hub import ServerOperationConflict
from services.ssh_manager import SSHManager
from services.system_cleanup_service import (
    LOG_CLEANUP_ACTION,
    LOG_CLEANUP_TASK_NAME,
    manual_execute_commands,
    manual_setup_commands,
    needs_privilege,
    normalize_schedule_value,
    normalize_targets,
    system_cleanup_service,
)

from .schemas import (
    CleanupDeleteBody,
    CleanupItemView,
    CleanupPolicyBody,
    CleanupPolicyView,
    CleanupScanView,
    CleanupSystemApplyBody,
    CleanupSystemScanView,
    CleanupSystemTargetView,
    CleanupWorkshopView,
    ServerOperationView,
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
        safe_items = [_item(item) for item in payload.get("safe_items") or []]
        archive_items = [_item(item) for item in payload.get("archive_items") or []]
        return CleanupScanView(
            safe_items=safe_items,
            archive_items=archive_items,
            workshop_summary=CleanupWorkshopView(
                path=str(workshop.get("path") or ""),
                item_count=int(workshop.get("item_count") or 0),
                size=int(workshop.get("size") or 0),
            ),
            total_size=int(payload.get("total_size") or 0),
            safe_item_count=int(payload.get("safe_item_count") or len(safe_items)),
            archive_item_count=int(payload.get("archive_item_count") or len(archive_items)),
            truncated=bool(payload.get("truncated")),
        )
    workshop = getattr(payload, "workshop_summary", None)
    safe_items = [_item(item) for item in getattr(payload, "safe_items", []) or []]
    archive_items = [_item(item) for item in getattr(payload, "archive_items", []) or []]
    return CleanupScanView(
        safe_items=safe_items,
        archive_items=archive_items,
        workshop_summary=CleanupWorkshopView(
            path=str(getattr(workshop, "path", "") or ""),
            item_count=int(getattr(workshop, "item_count", 0) or 0),
            size=int(getattr(workshop, "size", 0) or 0),
        ),
        total_size=int(getattr(payload, "total_size", 0) or 0),
        safe_item_count=int(getattr(payload, "safe_item_count", 0) or len(safe_items)),
        archive_item_count=int(getattr(payload, "archive_item_count", 0) or len(archive_items)),
        truncated=bool(getattr(payload, "truncated", False)),
    )


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


async def _load_stream_server(server_id: int, user):
    """Load the server with a short-lived session, then drop it before SSE starts."""
    async with async_session_maker() as db:
        server = await get_server_with_permission(server_id, user, db)
        db.expunge(server)
        return server


def _stream_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


@router.get("/{server_id}/cleanup/scan", response_model=CleanupScanView)
async def scan_server_cleanup(
    server_id: int, db: DatabaseSession, current_user: ActiveUser
) -> CleanupScanView:
    return _scan_view(await legacy.scan_server_cleanup(server_id, db, current_user))


@router.get("/{server_id}/cleanup/scan/events", response_model=None)
async def scan_server_cleanup_events(
    server_id: int,
    request: Request,
    current_user: StreamUser,
) -> StreamingResponse:
    server = await _load_stream_server(server_id, current_user)

    async def event_source():
        yield ": connected\n\n"
        ssh_manager = SSHManager()
        try:
            async for event in game_cleanup_service.iter_scan(ssh_manager, server):
                if await request.is_disconnected():
                    return
                kind = str(event.get("type") or "progress")
                if kind == "heartbeat":
                    yield ": keep-alive\n\n"
                    continue
                if kind == "done":
                    view = _scan_view(event.get("data") or {})
                    yield _sse("done", view.model_dump(mode="json"))
                    return
                yield _sse(kind, event)
        except Exception as exc:
            yield _sse("error", {"type": "error", "message": str(exc)})
        finally:
            try:
                await ssh_manager.disconnect()
            except Exception:
                pass

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers=_stream_headers(),
    )


@router.post(
    "/{server_id}/cleanup/delete",
    response_model=ServerOperationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_server_cleanup_items(
    server_id: int,
    body: CleanupDeleteBody,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    await get_server_with_permission(server_id, current_user, db)
    await reject_stuck_lock_unless_active(server_id)
    try:
        record = await enqueue_cleanup_delete(
            server_id=server_id,
            actor_user_id=current_user.id,
            mode=body.mode,
            paths=list(body.paths or []),
            confirmation_text=body.confirmation_text,
        )
    except ServerOperationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await record_audit_event(
        category="files",
        action="files.cleanup",
        status="requested",
        user=current_user,
        request=request,
        server_id=server_id,
        details={
            "operation_id": record["operation_id"],
            "mode": body.mode,
            "path_count": len(body.paths or []),
        },
    )
    return to_view(record)


async def _cleanup_task(db, server_id: int) -> ScheduledTask | None:
    result = await db.execute(
        select(ScheduledTask)
        .where(
            ScheduledTask.server_id == server_id,
            ScheduledTask.action == LOG_CLEANUP_ACTION,
        )
        .order_by(col(ScheduledTask.id).asc())
    )
    return result.scalars().first()


def _policy_view(payload: dict, extras: dict | None = None) -> CleanupPolicyView:
    data = dict(payload)
    if extras:
        data.update(extras)
    return CleanupPolicyView(**data)


def _system_scan_view(payload: dict) -> CleanupSystemScanView:
    return CleanupSystemScanView(
        privilege=payload["privilege"],
        retain_days=int(payload["retain_days"]),
        has_sudo_password=bool(payload.get("has_sudo_password")),
        targets=[CleanupSystemTargetView(**item) for item in payload.get("targets") or []],
        total_size=int(payload.get("total_size") or 0),
        can_apply_privileged=bool(payload.get("can_apply_privileged")),
        manual_execute=list(payload.get("manual_execute") or []),
        manual_setup=list(payload.get("manual_setup") or []),
    )


@router.get("/{server_id}/cleanup/policy", response_model=CleanupPolicyView)
async def get_cleanup_policy(
    server_id: int, db: DatabaseSession, current_user: ActiveUser
) -> CleanupPolicyView:
    server = await get_server_with_permission(server_id, current_user, db)
    task = await _cleanup_task(db, server_id)
    payload = system_cleanup_service.policy_from_server(server, task)
    extras = {}
    if (
        payload["enabled"]
        and any(needs_privilege(item) for item in payload["targets"])
        and not payload["has_sudo_password"]
    ):
        extras["manual_execute"] = manual_execute_commands(
            payload["targets"], payload["retain_days"]
        )
        extras["manual_setup"] = manual_setup_commands(
            payload["targets"], payload["retain_days"], payload["schedule_value"]
        )
    return _policy_view(payload, extras)


@router.put("/{server_id}/cleanup/policy", response_model=CleanupPolicyView)
async def update_cleanup_policy(
    server_id: int,
    body: CleanupPolicyBody,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> CleanupPolicyView:
    server = await get_server_with_permission(server_id, current_user, db)
    try:
        targets = normalize_targets(body.targets)
        schedule_value = normalize_schedule_value(body.schedule_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    task = await _cleanup_task(db, server_id)
    if task is None:
        task = ScheduledTask(
            server_id=server_id,
            name=LOG_CLEANUP_TASK_NAME,
            action=LOG_CLEANUP_ACTION,
            enabled=body.enabled,
            schedule_type="daily",
            schedule_value=schedule_value,
        )
    task.name = LOG_CLEANUP_TASK_NAME
    task.action = LOG_CLEANUP_ACTION
    task.enabled = body.enabled
    task.schedule_type = "daily"
    task.schedule_value = schedule_value
    now = get_current_time()
    try:
        hour, minute = (int(part) for part in schedule_value.split(":", 1))
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        task.next_run = next_run if body.enabled else None
    except OverflowError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Cleanup schedule is invalid",
        ) from exc

    server.cleanup_auto_enabled = body.enabled
    server.cleanup_retain_days = body.retain_days
    server.cleanup_targets = targets
    db.add(server)
    db.add(task)
    await db.commit()
    await db.refresh(task)

    extras: dict = {"message": "Cleanup policy saved."}
    if body.enabled and any(needs_privilege(item) for item in targets) and not server.sudo_password:
        extras["manual_execute"] = manual_execute_commands(targets, body.retain_days)
        extras["manual_setup"] = manual_setup_commands(targets, body.retain_days, schedule_value)
        extras["message"] = (
            "Policy saved. Privileged targets need root/sudo — "
            "save a sudo password on Host config, or run the manual commands."
        )
    await record_audit_event(
        category="config",
        action="config.cleanup.policy",
        status="success",
        user=current_user,
        request=request,
        server_id=server_id,
        details={
            "enabled": body.enabled,
            "retain_days": body.retain_days,
            "schedule_value": schedule_value,
            "targets": list(targets),
        },
    )
    return _policy_view(system_cleanup_service.policy_from_server(server, task), extras)


@router.get("/{server_id}/cleanup/system", response_model=CleanupSystemScanView)
async def scan_system_cleanup(
    server_id: int, db: DatabaseSession, current_user: ActiveUser
) -> CleanupSystemScanView:
    server = await get_server_with_permission(server_id, current_user, db)
    ssh_manager = SSHManager()
    try:
        try:
            payload = await system_cleanup_service.scan(ssh_manager, server)
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return _system_scan_view(payload)
    finally:
        try:
            await ssh_manager.disconnect()
        except Exception:
            pass


@router.get("/{server_id}/cleanup/system/events", response_model=None)
async def scan_system_cleanup_events(
    server_id: int,
    request: Request,
    current_user: StreamUser,
) -> StreamingResponse:
    server = await _load_stream_server(server_id, current_user)

    async def event_source():
        yield ": connected\n\n"
        ssh_manager = SSHManager()
        try:
            async for event in system_cleanup_service.iter_scan(ssh_manager, server):
                if await request.is_disconnected():
                    return
                kind = str(event.get("type") or "progress")
                if kind == "heartbeat":
                    yield ": keep-alive\n\n"
                    continue
                if kind == "done":
                    raw_data = event.get("data")
                    view = _system_scan_view(raw_data if isinstance(raw_data, dict) else {})
                    yield _sse("done", view.model_dump(mode="json"))
                    return
                yield _sse(kind, event)
        except Exception as exc:
            yield _sse("error", {"type": "error", "message": str(exc)})
        finally:
            try:
                await ssh_manager.disconnect()
            except Exception:
                pass

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers=_stream_headers(),
    )


@router.post(
    "/{server_id}/cleanup/system",
    response_model=ServerOperationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def apply_system_cleanup(
    server_id: int,
    body: CleanupSystemApplyBody,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    await get_server_with_permission(server_id, current_user, db)
    try:
        targets = normalize_targets(body.targets)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    await reject_stuck_lock_unless_active(server_id)
    try:
        record = await enqueue_cleanup_system(
            server_id=server_id,
            actor_user_id=current_user.id,
            targets=list(targets),
            retain_days=body.retain_days,
        )
    except ServerOperationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await record_audit_event(
        category="files",
        action="files.cleanup_system",
        status="requested",
        user=current_user,
        request=request,
        server_id=server_id,
        details={"operation_id": record["operation_id"], "targets": list(targets)},
    )
    return to_view(record)
