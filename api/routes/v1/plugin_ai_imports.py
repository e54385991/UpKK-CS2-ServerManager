"""Administrator AI marketplace discovery jobs."""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from api.contracts.v1.plugins import (
    PluginAIImportRequest,
    PluginAIImportView,
    PluginAIReadinessView,
    PluginAIReviewRequest,
    PluginAIReviewView,
)
from api.dependencies import AdminUser, StreamUser
from services.plugins import ai_import_store as store

router = APIRouter(prefix="/api/v1/plugins/market/ai-imports", tags=["v1-plugin-ai-imports"])


def to_view(job: store.JobSnapshot) -> PluginAIImportView:
    return PluginAIImportView(
        operation_id=job.operation_id,
        status=job.status,
        command=job.command,
        options=job.options,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        phase=job.phase,
        message=job.message,
        current_repository=job.current_repository,
        model=job.model,
        stop_reason=job.stop_reason,
        retry_at=job.retry_at,
        cancel_requested=job.cancel_requested,
        items=job.items,
        events=job.events,
    )


@router.get("/readiness", response_model=PluginAIReadinessView)
async def readiness(current_user: AdminUser) -> PluginAIReadinessView:
    verification, config = await store.readiness(current_user.id)
    return PluginAIReadinessView(
        token_valid=verification.valid,
        token_account=verification.account,
        token_message=verification.message,
        ai_configured=config is not None,
        ai_model=config.model if config else None,
    )


@router.post("", response_model=PluginAIImportView, status_code=202)
async def submit(body: PluginAIImportRequest, current_user: AdminUser) -> PluginAIImportView:
    if not body.acknowledge_ai_warning:
        raise HTTPException(422, "Acknowledge that AI installation settings require review")
    try:
        job = await store.enqueue(current_user.id, body.options, body.request_id)
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return to_view(job)


@router.get("", response_model=list[PluginAIImportView])
async def list_imports(current_user: AdminUser) -> list[PluginAIImportView]:
    return [to_view(job) for job in await store.list_jobs()]


@router.get("/{operation_id}", response_model=PluginAIImportView)
async def get_import(operation_id: UUID, current_user: AdminUser) -> PluginAIImportView:
    job = await store.get_job(str(operation_id))
    if job is None:
        raise HTTPException(404, "Import task not found")
    return to_view(job)


@router.post("/{operation_id}/cancel", response_model=PluginAIImportView)
async def cancel_import(operation_id: UUID, current_user: AdminUser) -> PluginAIImportView:
    try:
        job = await store.cancel_job(str(operation_id), current_user.id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return to_view(job)


@router.get("/{operation_id}/events", response_model=None)
async def events(
    operation_id: UUID, request: Request, current_user: StreamUser
) -> StreamingResponse:
    if not current_user.is_admin:
        raise HTTPException(403, "Administrator access required")
    if await store.get_job(str(operation_id)) is None:
        raise HTTPException(404, "Import task not found")

    async def source():
        last = ""
        while not await request.is_disconnected():
            try:
                await store.check_administrator(current_user.id)
            except PermissionError:
                return
            job = await store.get_job(str(operation_id))
            if job is None:
                return
            payload = to_view(job).model_dump(mode="json")
            encoded = json.dumps(payload, ensure_ascii=False)
            if encoded != last:
                last = encoded
                sequence = job.events[-1].sequence if job.events else 0
                yield f"id: {sequence}\nevent: snapshot\ndata: {encoded}\n\n"
            else:
                yield ": heartbeat\n\n"
            if job.status not in store.ACTIVE:
                return
            await asyncio.sleep(1)

    return StreamingResponse(
        source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.patch("/plugins/{plugin_id}/review", response_model=PluginAIReviewView)
async def review_plugin(
    plugin_id: int, body: PluginAIReviewRequest, current_user: AdminUser
) -> PluginAIReviewView:
    try:
        result = await store.review_plugin(plugin_id, current_user.id, body.metadata)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return PluginAIReviewView(metadata=result)
