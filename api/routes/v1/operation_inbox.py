"""Cross-server inbox of queued, running, and retained failed operations."""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlmodel import select

from api.dependencies import ActiveUser, DatabaseSession, StreamUser
from modules import Server
from services.server_operation_hub import (
    ACTIVE_STATUSES,
    FAILED_RETENTION_SECONDS,
    server_operation_hub,
)

from .operations import to_view
from .schemas import ActionResult, OperationInboxItem, OperationInboxView

router = APIRouter(prefix="/api/v1/operations", tags=["v1-operations"])


async def _accessible_servers(db, current_user) -> list[tuple[int, str]]:
    if current_user.is_admin:
        result = await db.execute(select(Server.id, Server.name))
    else:
        result = await db.execute(
            select(Server.id, Server.name).where(Server.user_id == current_user.id)
        )
    return [(int(row[0]), str(row[1])) for row in result.all()]


async def _to_inbox_item(
    record: dict,
    *,
    server_name: str,
    queue_position: int,
) -> OperationInboxItem:
    view = to_view(record)
    return OperationInboxItem(
        **view.model_dump(),
        server_name=server_name,
        latest_message=await server_operation_hub.latest_message(str(record["operation_id"])),
        queue_position=queue_position,
    )


async def _build_inbox(servers: list[tuple[int, str]]) -> OperationInboxView:
    names = {server_id: name for server_id, name in servers}
    items: list[OperationInboxItem] = []
    failed_items: list[OperationInboxItem] = []
    for server_id, server_name in servers:
        label = server_name or names.get(server_id) or f"#{server_id}"
        records = await server_operation_hub.list_for_server(server_id)
        for position, record in enumerate(records):
            if record.get("status") not in ACTIVE_STATUSES:
                continue
            items.append(
                await _to_inbox_item(
                    record,
                    server_name=label,
                    queue_position=position if record.get("status") == "queued" else 0,
                )
            )
        for record in await server_operation_hub.list_failed_for_server(server_id):
            failed_items.append(await _to_inbox_item(record, server_name=label, queue_position=0))
    items.sort(
        key=lambda item: (
            0 if item.status == "running" else 1,
            item.started_at,
        )
    )
    failed_items.sort(key=lambda item: item.completed_at or item.started_at, reverse=True)
    running = [item for item in items if item.status == "running"]
    return OperationInboxView(
        items=items,
        failed_items=failed_items,
        active_count=len(items),
        running_count=len(running),
        failed_count=len(failed_items),
        failed_retention_days=FAILED_RETENTION_SECONDS // 86400,
    )


@router.get("/inbox", response_model=OperationInboxView)
async def list_operation_inbox(
    db: DatabaseSession,
    current_user: ActiveUser,
) -> OperationInboxView:
    """Active jobs plus failed jobs retained for seven days."""
    return await _build_inbox(await _accessible_servers(db, current_user))


@router.get("/inbox/events")
async def stream_operation_inbox(
    request: Request,
    db: DatabaseSession,
    current_user: StreamUser,
) -> StreamingResponse:
    """Live inbox snapshots for the top-right tray. SSE, not a second WebSocket."""
    servers = await _accessible_servers(db, current_user)

    async def event_source():
        yield ": connected\n\n"
        last = ""
        idle_ticks = 0
        while not await request.is_disconnected():
            view = await _build_inbox(servers)
            encoded = json.dumps(
                view.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            if encoded != last:
                last = encoded
                idle_ticks = 0
                yield f"event: inbox\ndata: {encoded}\n\n"
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


@router.delete("/inbox/failed", response_model=ActionResult)
async def clear_failed_operations(
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ActionResult:
    """Remove every retained failure the caller can see."""
    servers = await _accessible_servers(db, current_user)
    cleared = await server_operation_hub.clear_failed([server_id for server_id, _name in servers])
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
    allowed = {server_id for server_id, _name in await _accessible_servers(db, current_user)}
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
