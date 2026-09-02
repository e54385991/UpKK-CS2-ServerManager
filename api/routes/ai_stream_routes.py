"""SSE and WebSocket event routes for AI runs."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.dependencies import ActiveUser, DatabaseSession, close_request_session
from modules import AIRun, User, async_session_maker, authenticate_websocket
from services.ai_events import ai_event_hub

from .ai import router


async def _run_for_user(db: AsyncSession, user: User, run_id: str) -> AIRun:
    result = await db.execute(
        select(AIRun).where(AIRun.id == run_id, AIRun.user_id == user.id, AIRun.source == "web")
    )
    run = result.scalar_one_or_none()
    if run is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


def _encode_sse_event(event: dict[str, Any]) -> str:
    sequence = str(event.get("sequence") or "0")
    event_type = str(event.get("type") or "message").replace("\r", "").replace("\n", "")
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"id: {sequence}\nevent: {event_type}\ndata: {data}\n\n"


@router.get("/api/ai/runs/{run_id}/events/stream")
async def ai_run_event_stream(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    *,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> StreamingResponse:
    await _run_for_user(db, current_user, run_id)
    await close_request_session(db)

    async def event_source():
        queue = await ai_event_hub.subscribe_queue(run_id)
        latest_sequence = after
        try:
            yield ": connected\n\n"
            for event in await ai_event_hub.replay(run_id, latest_sequence):
                sequence = int(event.get("sequence") or 0)
                if sequence <= latest_sequence:
                    continue
                latest_sequence = sequence
                yield _encode_sse_event(event)
            idle_ticks = 0
            while not await request.is_disconnected():
                pending_events: list[dict[str, Any]] = []
                try:
                    pending_events.append(await asyncio.wait_for(queue.get(), timeout=1))
                except TimeoutError:
                    idle_ticks += 1
                    if idle_ticks >= 15:
                        idle_ticks = 0
                        yield ": keep-alive\n\n"
                else:
                    idle_ticks = 0
                # A run task and its SSE client may live in different workers.
                # Redis replay turns the local queue into a low-latency wake-up,
                # while still delivering cross-process events within one second.
                pending_events.extend(await ai_event_hub.replay(run_id, latest_sequence))
                pending_events.sort(key=lambda item: int(item.get("sequence") or 0))
                for event in pending_events:
                    sequence = int(event.get("sequence") or 0)
                    if sequence <= latest_sequence:
                        continue
                    latest_sequence = sequence
                    yield _encode_sse_event(event)
                    if event.get("type") in {"run_completed", "run_failed", "run_interrupted"}:
                        return
        finally:
            await ai_event_hub.unsubscribe_queue(run_id, queue)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/api/ai/runs/{run_id}/events")
async def ai_run_events(
    websocket: WebSocket,
    run_id: str,
    after: int = Query(default=0, ge=0),
) -> None:
    user, _ = await authenticate_websocket(websocket)
    if user is None:
        return
    async with async_session_maker() as db:
        run_result = await db.execute(
            select(AIRun).where(
                AIRun.id == run_id,
                AIRun.user_id == user.id,
                AIRun.source == "web",
            )
        )
        run = run_result.scalar_one_or_none()
        if run is None:
            await websocket.close(code=4404, reason="Run not found")
            return
    await websocket.accept()
    await ai_event_hub.subscribe(run_id, websocket)
    try:
        for event in await ai_event_hub.replay(run_id, after):
            await websocket.send_json(event)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await ai_event_hub.unsubscribe(run_id, websocket)
