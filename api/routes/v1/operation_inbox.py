"""Cross-server inbox of queued, running, and retained operations."""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from api.dependencies import ActiveUser, DatabaseSession, StreamUser, close_request_session
from services.operations.inbox import (
    build_operation_inbox,
    list_accessible_servers,
    load_accessible_servers,
)
from services.operations.inbox_sse import InboxSseState, inbox_sse_headers, next_inbox_sse_frame
from services.operations.inbox_types import InboxItemData, InboxPayload
from services.plugins.ai_import_store import check_administrator, clear_failed_jobs
from services.plugins.description_sync_store import (
    clear_failed_jobs as clear_failed_description_jobs,
)
from services.server_operation_hub import server_operation_hub

from .operations import to_view
from .plugin_ai_imports import to_view as import_view
from .plugins import _description_sync_view as description_sync_view
from .schemas import ActionResult, OperationInboxItem, OperationInboxView

router = APIRouter(prefix="/api/v1/operations", tags=["v1-operations"])


def _to_inbox_item(item: InboxItemData) -> OperationInboxItem:
    view = to_view(item.record)
    return OperationInboxItem(
        **view.model_dump(),
        server_name=item.server_name,
        latest_message=item.latest_message,
        queue_position=item.queue_position,
    )


def to_inbox_view(payload: InboxPayload) -> OperationInboxView:
    items = [_to_inbox_item(item) for item in payload.items]
    completed_items = [_to_inbox_item(item) for item in payload.completed_items]
    failed_items = [_to_inbox_item(item) for item in payload.failed_items]
    running = [item for item in items if item.status == "running"]
    return OperationInboxView(
        market_import_items=[import_view(job) for job in payload.import_jobs],
        market_description_items=[description_sync_view(job) for job in payload.description_jobs],
        items=items,
        completed_items=completed_items,
        failed_items=failed_items,
        active_count=len(items),
        running_count=len(running),
        completed_count=len(completed_items),
        failed_count=len(failed_items),
        completed_retention_days=payload.completed_retention_days,
        failed_retention_days=payload.failed_retention_days,
    )


def encode_inbox_view(view: OperationInboxView) -> str:
    OperationInboxView.model_validate(view.model_dump())
    return json.dumps(
        view.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _encode_inbox_payload(payload: InboxPayload) -> str:
    return encode_inbox_view(to_inbox_view(payload))


async def _build_inbox(
    servers: list[tuple[int, str]], include_imports: bool = False
) -> OperationInboxView:
    return to_inbox_view(await build_operation_inbox(servers, include_imports=include_imports))


@router.get("/inbox", response_model=OperationInboxView)
async def list_operation_inbox(
    db: DatabaseSession,
    current_user: ActiveUser,
) -> OperationInboxView:
    """Active jobs plus completed and failed history retained for seven days."""
    servers = await load_accessible_servers(db, current_user)
    await close_request_session(db)
    return await _build_inbox(servers, current_user.is_admin)


@router.get("/inbox/events", response_model=None)
async def stream_operation_inbox(
    request: Request,
    current_user: StreamUser,
) -> StreamingResponse:
    """Live inbox snapshots for the top-right tray. SSE, not a second WebSocket."""
    servers = await list_accessible_servers(current_user)

    async def event_source():
        yield ": connected\n\n"
        state = InboxSseState()
        while not await request.is_disconnected():
            include_imports = current_user.is_admin
            if include_imports:
                try:
                    await check_administrator(current_user.id)
                except PermissionError:
                    include_imports = False
            payload = await build_operation_inbox(servers, include_imports=include_imports)
            frame = next_inbox_sse_frame(state, payload, _encode_inbox_payload)
            if frame is not None:
                yield frame.text
            await asyncio.sleep(1)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers=inbox_sse_headers(),
    )


@router.delete("/inbox/failed", response_model=ActionResult)
async def clear_failed_operations(
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ActionResult:
    """Remove every retained failure the caller can see.

    Administrators also see failed AI marketplace imports in the same tray, so
    the one-click clear covers those too — they were otherwise undismissable
    and kept the tray badge red for their full seven-day retention.
    """
    servers = await load_accessible_servers(db, current_user)
    await close_request_session(db)
    cleared = await server_operation_hub.clear_failed([server_id for server_id, _name in servers])
    if current_user.is_admin:
        try:
            cleared += await clear_failed_jobs(current_user.id)
        except PermissionError:
            pass
        try:
            cleared += await clear_failed_description_jobs(current_user.id)
        except PermissionError:
            pass
    return ActionResult(
        success=True,
        message=f"Cleared {cleared} failed operation(s)",
    )


@router.delete("/inbox/failed/{operation_id}", response_model=ActionResult)
async def dismiss_failed_operation(
    operation_id: UUID,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ActionResult:
    """Remove one failed job from the retained failure tab."""
    allowed = {server_id for server_id, _name in await load_accessible_servers(db, current_user)}
    await close_request_session(db)
    record = await server_operation_hub.get(str(operation_id))
    if record is None or record.get("status") != "failed":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Failed operation not found"
        )
    if int(record["server_id"]) not in allowed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Failed operation not found"
        )
    dismissed = await server_operation_hub.dismiss_failed(str(operation_id))
    if dismissed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Failed operation not found"
        )
    return ActionResult(success=True, message="Failed operation cleared")


@router.delete("/inbox/completed", response_model=ActionResult)
async def clear_completed_operations(
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ActionResult:
    """Remove every retained successful job the caller can see."""
    servers = await load_accessible_servers(db, current_user)
    await close_request_session(db)
    cleared = await server_operation_hub.clear_completed(
        [server_id for server_id, _name in servers]
    )
    return ActionResult(
        success=True,
        message=f"Cleared {cleared} completed operation(s)",
    )


@router.delete("/inbox/completed/{operation_id}", response_model=ActionResult)
async def dismiss_completed_operation(
    operation_id: UUID,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ActionResult:
    """Remove one completed job from the retained history."""
    allowed = {server_id for server_id, _name in await load_accessible_servers(db, current_user)}
    await close_request_session(db)
    record = await server_operation_hub.get(str(operation_id))
    if record is None or record.get("status") != "completed":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Completed operation not found"
        )
    if int(record["server_id"]) not in allowed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Completed operation not found"
        )
    dismissed = await server_operation_hub.dismiss_completed(str(operation_id))
    if dismissed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Completed operation not found"
        )
    return ActionResult(success=True, message="Completed operation cleared")
