"""Versioned async server operations: 202 + operation_id + replayable SSE."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from api.dependencies import ActiveUser, DatabaseSession, StreamUser, require_server_access
from api.routes.actions.deployment import cancel_deployment
from modules import DeploymentLog
from services.ai_security import redact_sensitive_text
from services.maintenance_lock import maintenance_lock_service
from services.redis_manager import redis_manager
from services.server_operation_hub import (
    ACTIVE_STATUSES,
    TERMINAL_EVENT_TYPES,
    ServerOperationConflict,
    server_operation_hub,
)

from .operation_runner import enqueue_server_operation
from .schemas import (
    ActionResult,
    CurrentServerOperation,
    DeploymentLockView,
    DeploymentLogEntry,
    OperationJournal,
    OperationJournalEvent,
    ServerOperationRequest,
    ServerOperationView,
)

router = APIRouter(
    prefix="/api/v1/servers/{server_id}/operations",
    tags=["v1-operations"],
)

_LOG_FIELD_LIMIT = 4000


def _stream_url(server_id: int, operation_id: str) -> str:
    return f"/api/v1/servers/{server_id}/operations/{operation_id}/events"


def to_view(record: dict[str, Any]) -> ServerOperationView:
    command = record.get("command")
    return ServerOperationView(
        operation_id=str(record["operation_id"]),
        server_id=int(record["server_id"]),
        action=record["action"],
        status=record["status"],
        success=record.get("success"),
        message=record.get("message"),
        server_status=record.get("server_status"),
        started_at=record["started_at"],
        completed_at=record.get("completed_at"),
        actor_user_id=int(record["actor_user_id"]),
        stream_url=_stream_url(int(record["server_id"]), str(record["operation_id"])),
        command=str(command) if command else None,
    )


def _encode_sse_event(event: dict[str, Any]) -> str:
    sequence = event.get("sequence") or "0"
    event_type = event.get("type") or "progress"
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"id: {sequence}\nevent: {event_type}\ndata: {data}\n\n"


def _to_journal_event(event: dict[str, Any]) -> OperationJournalEvent:
    success = event.get("success")
    server_status = event.get("server_status")
    return OperationJournalEvent(
        sequence=str(event.get("sequence") or ""),
        operation_id=str(event.get("operation_id") or ""),
        type=str(event.get("type") or "progress"),
        kind=str(event.get("kind") or "output"),
        message=str(event.get("message") or ""),
        timestamp=str(event.get("timestamp") or ""),
        success=success if isinstance(success, bool) else None,
        server_status=str(server_status) if server_status else None,
    )


@router.post("", response_model=ServerOperationView, status_code=status.HTTP_202_ACCEPTED)
async def start_server_operation(
    server_id: int,
    body: ServerOperationRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    """Accept a lifecycle action and return immediately with an operation_id."""
    await require_server_access(db, server_id, current_user)

    current = await server_operation_hub.get_current(server_id)
    current_active = bool(current and current.get("status") in ACTIVE_STATUSES)
    if not current_active:
        if await redis_manager.get(f"deployment_lock:{server_id}"):
            await redis_manager.delete(f"deployment_lock:{server_id}")
        if await maintenance_lock_service.is_locked(server_id):
            await maintenance_lock_service.force_release_server_lock(server_id)

    try:
        record = await enqueue_server_operation(
            server_id=server_id,
            action=body.action,
            actor_user_id=current_user.id,
            request=request,
        )
    except ServerOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return to_view(record)


@router.get("/current", response_model=CurrentServerOperation)
async def get_current_server_operation(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> CurrentServerOperation:
    await require_server_access(db, server_id, current_user)
    record = await server_operation_hub.get_current(server_id)
    return CurrentServerOperation(operation=to_view(record) if record else None)


@router.get("/logs", response_model=list[DeploymentLogEntry])
async def list_operation_logs(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[DeploymentLogEntry]:
    await require_server_access(db, server_id, current_user)
    logs = await DeploymentLog.get_logs_by_server(db, server_id, skip, limit)
    entries: list[DeploymentLogEntry] = []
    for log in logs:
        output = redact_sensitive_text(log.output, limit=_LOG_FIELD_LIMIT) if log.output else None
        error = (
            redact_sensitive_text(log.error_message, limit=_LOG_FIELD_LIMIT)
            if log.error_message
            else None
        )
        entries.append(
            DeploymentLogEntry(
                id=log.id,
                action=log.action,
                status=log.status,
                output=output,
                error_message=error,
                created_at=log.created_at,
            )
        )
    return entries


@router.get("/lock", response_model=DeploymentLockView)
async def get_deployment_lock(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> DeploymentLockView:
    server = await require_server_access(db, server_id, current_user)
    lock_exists = await redis_manager.get(f"deployment_lock:{server_id}")
    return DeploymentLockView(lock_active=bool(lock_exists), server_status=server.status)


@router.delete("/lock", response_model=ActionResult)
async def clear_deployment_lock(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ActionResult:
    response = await cancel_deployment(server_id, db, current_user)
    payload = json.loads(response.body)
    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=str(payload.get("message") or payload.get("detail") or "Failed to clear lock"),
        )
    return ActionResult(
        success=bool(payload.get("success")),
        message=str(payload.get("message") or "Deployment lock cleared"),
    )


@router.get("/{operation_id}", response_model=ServerOperationView)
async def get_server_operation(
    server_id: int,
    operation_id: UUID,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    await require_server_access(db, server_id, current_user)
    record = await server_operation_hub.get(str(operation_id))
    if record is None or int(record["server_id"]) != server_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    return to_view(record)


@router.get("/{operation_id}/journal", response_model=OperationJournal)
async def get_server_operation_journal(
    server_id: int,
    operation_id: UUID,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> OperationJournal:
    """JSON snapshot of persisted progress so the live log never depends on SSE."""
    await require_server_access(db, server_id, current_user)
    op_id = str(operation_id)
    record = await server_operation_hub.get(op_id)
    if record is None or int(record["server_id"]) != server_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    events = await server_operation_hub.replay(op_id, 0)
    return OperationJournal(
        operation=to_view(record),
        events=[_to_journal_event(event) for event in events],
    )


@router.get("/{operation_id}/events")
async def stream_server_operation_events(
    server_id: int,
    operation_id: UUID,
    request: Request,
    db: DatabaseSession,
    current_user: StreamUser,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    await require_server_access(db, server_id, current_user)
    op_id = str(operation_id)
    record = await server_operation_hub.get(op_id)
    if record is None or int(record["server_id"]) != server_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

    async def event_source():
        queue = await server_operation_hub.subscribe_queue(op_id)
        sequence = after
        try:
            yield ": connected\n\n"
            replayed = await server_operation_hub.replay(op_id, sequence)
            if not replayed:
                current = await server_operation_hub.get(op_id)
                if current and current.get("status") in ACTIVE_STATUSES:
                    await server_operation_hub.emit(
                        op_id,
                        "progress",
                        kind="status",
                        message="Live log connected; waiting for worker output…",
                    )
                    replayed = await server_operation_hub.replay(op_id, sequence)
            for event in replayed:
                try:
                    event_sequence = int(event.get("sequence") or 0)
                except TypeError, ValueError:
                    continue
                if event_sequence <= sequence:
                    continue
                sequence = event_sequence
                yield _encode_sse_event(event)
                if event.get("type") in TERMINAL_EVENT_TYPES:
                    return
            idle_ticks = 0
            while not await request.is_disconnected():
                pending: list[dict[str, Any]] = []
                try:
                    pending.append(await asyncio.wait_for(queue.get(), timeout=1))
                except TimeoutError:
                    idle_ticks += 1
                    if idle_ticks >= 15:
                        idle_ticks = 0
                        yield ": keep-alive\n\n"
                    current = await server_operation_hub.get(op_id)
                    if current and current.get("status") in {"completed", "failed"}:
                        for event in await server_operation_hub.replay(op_id, sequence):
                            yield _encode_sse_event(event)
                        return
                else:
                    idle_ticks = 0
                pending.extend(await server_operation_hub.replay(op_id, sequence))
                pending.sort(key=lambda item: int(item.get("sequence") or 0))
                for event in pending:
                    try:
                        event_sequence = int(event.get("sequence") or 0)
                    except TypeError, ValueError:
                        continue
                    if event_sequence <= sequence:
                        continue
                    sequence = event_sequence
                    yield _encode_sse_event(event)
                    if event.get("type") in TERMINAL_EVENT_TYPES:
                        return
        finally:
            await server_operation_hub.unsubscribe_queue(op_id, queue)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
