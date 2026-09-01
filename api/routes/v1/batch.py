"""Versioned fleet batch actions with a replayable Redis journal + SSE."""

from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from api.dependencies import ActiveUser, DatabaseSession, StreamUser
from api.routes.actions.common import (
    _reserve_batch_capacity,
    _run_bounded_batch_operation,
    _store_task,
    execute_single_server_action,
    execute_single_server_command,
    execute_single_server_plugins,
)
from services.audit_log_service import record_audit_event
from services.redis_manager import redis_manager
from services.servers.batch import authorized_server_ids

from .schemas import (
    BatchActionRequest,
    BatchActionView,
    BatchInstallPluginsRequest,
    BatchJournalView,
    BatchSendCommandRequest,
    BatchServerStatusView,
    BatchSummaryView,
)

router = APIRouter(prefix="/api/v1/servers", tags=["v1-batch"])


def _stream_url(batch_id: str) -> str:
    return f"/api/v1/servers/batch-actions/{batch_id}/events"


def _summary(statuses: dict[str, Any]) -> BatchSummaryView:
    total = len(statuses)
    completed = sum(1 for item in statuses.values() if item.get("status") in {"success", "failed"})
    succeeded = sum(1 for item in statuses.values() if item.get("status") == "success")
    failed = sum(1 for item in statuses.values() if item.get("status") == "failed")
    in_progress = sum(
        1 for item in statuses.values() if item.get("status") in {"pending", "in_progress"}
    )
    return BatchSummaryView(
        total=total,
        completed=completed,
        succeeded=succeeded,
        failed=failed,
        in_progress=in_progress,
        is_complete=total > 0 and completed == total,
    )


def _server_views(statuses: dict[str, Any]) -> list[BatchServerStatusView]:
    views: list[BatchServerStatusView] = []
    for server_id, payload in statuses.items():
        try:
            parsed_id = int(server_id)
        except TypeError, ValueError:
            continue
        item = payload if isinstance(payload, dict) else {}
        views.append(
            BatchServerStatusView(
                server_id=parsed_id,
                status=str(item.get("status") or "pending"),
                message=str(item.get("message") or ""),
            )
        )
    views.sort(key=lambda item: item.server_id)
    return views


async def _require_batch_journal(
    batch_id: str,
    current_user,
) -> BatchJournalView:
    meta = await redis_manager.get_batch_action_meta(batch_id)
    if not meta or int(meta.get("actor_user_id") or 0) != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Batch action not found or expired",
        )
    statuses = await redis_manager.get_batch_action_status(batch_id)
    if not statuses:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Batch action not found or expired",
        )
    return BatchJournalView(
        batch_id=batch_id,
        action=str(meta.get("action") or "") or None,
        servers=_server_views(statuses),
        summary=_summary(statuses),
    )


async def _start_batch(
    *,
    db,
    current_user,
    http_request: Request,
    server_ids: list[int],
    action: str,
    audit_action: str,
    audit_details: dict[str, Any],
    pending_message: str,
    operation_label: str,
    callback_factory,
    response_message: str,
    acquire_lock: bool = True,
) -> BatchActionView:
    valid_server_ids = await authorized_server_ids(db, server_ids, current_user.id)
    if not valid_server_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid servers found in the request",
        )

    batch_id = secrets.token_hex(16)
    await redis_manager.set_batch_action_meta(
        batch_id, actor_user_id=current_user.id, action=action
    )
    await redis_manager.set_batch_action_statuses(
        batch_id,
        valid_server_ids,
        "pending",
        pending_message,
    )
    await _reserve_batch_capacity(current_user.id, len(valid_server_ids))
    for server_id in valid_server_ids:
        task = asyncio.create_task(
            _run_bounded_batch_operation(
                server_id,
                current_user.id,
                batch_id,
                operation_label,
                callback_factory(server_id, batch_id),
                acquire_lock=acquire_lock,
            )
        )
        _store_task(task)

    await record_audit_event(
        category="server",
        action=audit_action,
        status="requested",
        user=current_user,
        request=http_request,
        details={**audit_details, "server_ids": valid_server_ids, "batch_id": batch_id},
    )
    return BatchActionView(
        batch_id=batch_id,
        action=action,
        server_count=len(valid_server_ids),
        accepted_server_ids=valid_server_ids,
        stream_url=_stream_url(batch_id),
        message=response_message.format(count=len(valid_server_ids)),
    )


@router.post(
    "/batch-actions",
    response_model=BatchActionView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_batch_actions(
    body: BatchActionRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    http_request: Request,
) -> BatchActionView:
    """Restart, stop, or update owned servers. Admins still cannot act on others' hosts."""
    return await _start_batch(
        db=db,
        current_user=current_user,
        http_request=http_request,
        server_ids=body.server_ids,
        action=body.action,
        audit_action=f"server.batch.{body.action}",
        audit_details={},
        pending_message="Queued for processing",
        operation_label=f"batch_action:{body.action}",
        callback_factory=lambda server_id, batch_id: (
            lambda: execute_single_server_action(
                server_id, body.action, current_user.id, current_user.is_admin, batch_id
            )
        ),
        response_message=f"Batch action '{body.action}' started for {{count}} server(s)",
        acquire_lock=False,
    )


@router.post(
    "/batch-install-plugins",
    response_model=BatchActionView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_batch_install_plugins(
    body: BatchInstallPluginsRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    http_request: Request,
) -> BatchActionView:
    """Install framework plugins on owned servers. Authorization matches legacy batch."""
    plugins = list(body.plugins)
    return await _start_batch(
        db=db,
        current_user=current_user,
        http_request=http_request,
        server_ids=body.server_ids,
        action="install_plugins",
        audit_action="server.batch.install_plugins",
        audit_details={"plugins": plugins},
        pending_message="Queued for plugin installation",
        operation_label="batch_plugin_install",
        callback_factory=lambda server_id, batch_id: (
            lambda: execute_single_server_plugins(
                server_id, plugins, current_user.id, current_user.is_admin, batch_id
            )
        ),
        response_message="Installing plugins on {count} server(s) in background",
        acquire_lock=False,
    )


@router.post(
    "/batch-send-command",
    response_model=BatchActionView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_batch_send_command(
    body: BatchSendCommandRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    http_request: Request,
) -> BatchActionView:
    """Send one game command to owned servers via each host's detached session."""
    command = body.command
    return await _start_batch(
        db=db,
        current_user=current_user,
        http_request=http_request,
        server_ids=body.server_ids,
        action="send_command",
        audit_action="server.batch.send_command",
        audit_details={"command_present": True},
        pending_message="Queued for command execution",
        operation_label="batch_command",
        callback_factory=lambda server_id, batch_id: (
            lambda: execute_single_server_command(
                server_id, command, current_user.id, current_user.is_admin, batch_id
            )
        ),
        response_message="Sending command to {count} server(s) in background",
    )


@router.get("/batch-actions/{batch_id}", response_model=BatchJournalView)
async def read_batch_action_journal(
    batch_id: str,
    current_user: ActiveUser,
) -> BatchJournalView:
    """JSON snapshot of every server in the batch. Actor-only, same as the live stream."""
    return await _require_batch_journal(batch_id, current_user)


@router.get("/batch-actions/{batch_id}/events", response_model=None)
async def stream_batch_action_events(
    batch_id: str,
    request: Request,
    current_user: StreamUser,
) -> StreamingResponse:
    """Replayable SSE snapshot of the Redis batch journal until every server finishes."""
    await _require_batch_journal(batch_id, current_user)

    async def event_source():
        yield ": connected\n\n"
        last = ""
        idle_ticks = 0
        while not await request.is_disconnected():
            try:
                view = await _require_batch_journal(batch_id, current_user)
            except HTTPException:
                return
            payload = view.model_dump(mode="json")
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
            if encoded != last:
                last = encoded
                idle_ticks = 0
                yield f"event: progress\ndata: {encoded}\n\n"
                if view.summary.is_complete:
                    yield f"event: complete\ndata: {encoded}\n\n"
                    return
            else:
                idle_ticks += 1
                if idle_ticks >= 15:
                    idle_ticks = 0
                    yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
