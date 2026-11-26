"""Versioned endpoints for bulk marketplace release sync and invalid-listing scans."""

from __future__ import annotations

import asyncio
import json
from typing import get_args
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from api.dependencies import AdminUser, StreamUser
from modules import PluginFramework
from services.audit_log_service import record_audit_event
from services.plugin_conflict_service import PluginPlanError
from services.plugins import release_sync_store
from services.plugins.common import parse_framework

from .schemas import (
    ActionResult,
    MarketPluginReleaseSyncItemView,
    MarketPluginReleaseSyncModeLiteral,
    MarketPluginReleaseSyncRequest,
    MarketPluginReleaseSyncView,
    PluginFrameworkLiteral,
    ProblemDetail,
)

router = APIRouter(prefix="/api/v1/plugins/market/releases/sync", tags=["v1-plugins"])


def _framework_literal(value: str | None) -> PluginFrameworkLiteral | None:
    for allowed in get_args(PluginFrameworkLiteral):
        if value == allowed:
            return allowed
    return None


def _parse_framework_filter(framework: str | None) -> PluginFramework | None:
    if not framework:
        return None
    try:
        return parse_framework(framework)
    except PluginPlanError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


def to_release_sync_view(
    job: release_sync_store.ReleaseSyncJobSnapshot,
) -> MarketPluginReleaseSyncView:
    return MarketPluginReleaseSyncView(
        operation_id=job.operation_id,
        mode=job.mode,
        status=job.status,
        command=job.command,
        framework=_framework_literal(job.framework),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        phase=job.phase,
        message=job.message,
        current_plugin_id=job.current_plugin_id,
        current_plugin_title=job.current_plugin_title,
        current_github_url=job.current_github_url,
        stop_reason=job.stop_reason,
        retry_at=job.retry_at,
        cancel_requested=job.cancel_requested,
        total=job.total,
        processed=job.processed,
        updated=job.updated,
        unchanged=job.unchanged,
        skipped=job.skipped,
        failed=job.failed,
        cleanable=job.cleanable,
        items=[
            MarketPluginReleaseSyncItemView(
                plugin_id=item.plugin_id,
                title=item.title,
                github_url=item.github_url,
                action=item.action,
                tag=item.tag,
                rule=item.rule,
                selected_assets=list(item.selected_assets),
                issue_code=item.issue_code,
                cleanable=item.cleanable,
                message=item.message,
            )
            for item in job.items
        ],
    )


@router.post(
    "",
    response_model=MarketPluginReleaseSyncView,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        403: {"model": ProblemDetail},
        409: {"model": ProblemDetail},
        503: {"model": ProblemDetail},
    },
)
async def enqueue_market_release_sync(
    body: MarketPluginReleaseSyncRequest,
    current_user: AdminUser,
    request: Request,
) -> MarketPluginReleaseSyncView:
    """Queue a throttled bulk release sync or invalid-listing scan."""
    framework = _parse_framework_filter(body.framework)
    try:
        job = await release_sync_store.enqueue(
            current_user.id,
            body.request_id,
            mode=body.mode,
            plugin_ids=list(body.plugin_ids) or None,
            framework=framework,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    audit_action = (
        "plugin.catalog.scan_invalid.requested"
        if body.mode == "scan"
        else "plugin.catalog.sync_releases.requested"
    )
    await record_audit_event(
        category="plugin",
        action=audit_action,
        status="requested",
        user=current_user,
        request=request,
        details={
            "operation_id": job.operation_id,
            "mode": body.mode,
            "total": job.total,
            "framework": body.framework,
            "selected_count": len(body.plugin_ids),
        },
    )
    return to_release_sync_view(job)


@router.get("", response_model=list[MarketPluginReleaseSyncView])
async def list_market_release_syncs(
    current_user: AdminUser,
    mode: MarketPluginReleaseSyncModeLiteral | None = Query(default=None),
) -> list[MarketPluginReleaseSyncView]:
    """List retained bulk release sync and invalid-listing scan tasks."""
    del current_user
    return [to_release_sync_view(job) for job in await release_sync_store.list_jobs(mode=mode)]


@router.get(
    "/{operation_id}",
    response_model=MarketPluginReleaseSyncView,
)
async def get_market_release_sync(
    operation_id: UUID,
    current_user: AdminUser,
) -> MarketPluginReleaseSyncView:
    del current_user
    job = await release_sync_store.get_job(str(operation_id))
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Release sync task not found",
        )
    return to_release_sync_view(job)


@router.post(
    "/{operation_id}/cancel",
    response_model=MarketPluginReleaseSyncView,
)
async def cancel_market_release_sync(
    operation_id: UUID,
    current_user: AdminUser,
) -> MarketPluginReleaseSyncView:
    try:
        job = await release_sync_store.cancel_job(str(operation_id), current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return to_release_sync_view(job)


@router.delete(
    "/{operation_id}",
    response_model=ActionResult,
)
async def delete_market_release_sync(
    operation_id: UUID,
    current_user: AdminUser,
) -> ActionResult:
    try:
        await release_sync_store.delete_job(str(operation_id), current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ActionResult(success=True, message="Release sync task deleted")


@router.get("/{operation_id}/events", response_model=None)
async def stream_market_release_sync(
    operation_id: UUID,
    request: Request,
    current_user: StreamUser,
) -> StreamingResponse:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required"
        )
    if await release_sync_store.get_job(str(operation_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Release sync task not found",
        )

    async def source():
        last = ""
        while not await request.is_disconnected():
            job = await release_sync_store.get_job(str(operation_id))
            if job is None:
                return
            payload = to_release_sync_view(job).model_dump(mode="json")
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if encoded != last:
                last = encoded
                yield f"event: snapshot\ndata: {encoded}\n\n"
            else:
                yield ": heartbeat\n\n"
            if job.status not in release_sync_store.ACTIVE:
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
