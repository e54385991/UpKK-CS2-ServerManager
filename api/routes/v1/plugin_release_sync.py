"""Marketplace release review and queued deployment endpoints."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status

from api.contracts.v1.plugin_release_sync import (
    MarketReleaseDeployItemView,
    MarketReleaseDeployRequest,
    MarketReleaseDeployView,
    MarketReleasePreviewRequest,
    MarketReleasePreviewView,
    MarketReleaseSyncRequest,
    MarketReleaseTargetsView,
)
from api.dependencies import AdminUser
from services.audit_log_service import record_audit_event
from services.plugins.github_assets import GitHubPlanError
from services.plugins.market_release_sync import (
    ReleaseListingMissing,
    ReleaseSyncUnavailable,
    preview_release_sync,
    release_targets,
    save_release_sync,
)
from services.redis_manager import redis_manager
from services.server_operation_hub import ServerOperationConflict

from .operation_locks import reject_stuck_lock_unless_active
from .operation_runner import enqueue_plugin_auto_update
from .operations import to_view

router = APIRouter(prefix="/api/v1/plugins/market/{plugin_id}/release-sync", tags=["v1-plugins"])


def _sync_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ReleaseListingMissing):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, ReleaseSyncUnavailable):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.post("/preview", response_model=MarketReleasePreviewView)
async def preview(
    plugin_id: int, body: MarketReleasePreviewRequest, user: AdminUser
) -> MarketReleasePreviewView:
    try:
        data = await preview_release_sync(
            plugin_id, user, rule=body.rule, asset_name=body.asset_name
        )
    except (ReleaseListingMissing, ReleaseSyncUnavailable, GitHubPlanError) as exc:
        raise _sync_error(exc) from exc
    return MarketReleasePreviewView.model_validate(data)


async def _targets(plugin_id: int, user: AdminUser) -> MarketReleaseTargetsView:
    try:
        snapshot, targets = await release_targets(plugin_id, user)
    except (ReleaseListingMissing, GitHubPlanError) as exc:
        raise _sync_error(exc) from exc
    return MarketReleaseTargetsView.model_validate(
        {
            "revision": snapshot.revision,
            "tag": snapshot.tag,
            "targets": [
                {key: value for key, value in target.items() if key != "managed_plugin_id"}
                for target in targets
            ],
        }
    )


@router.get("/targets", response_model=MarketReleaseTargetsView)
async def targets(plugin_id: int, user: AdminUser) -> MarketReleaseTargetsView:
    return await _targets(plugin_id, user)


@router.post("", response_model=MarketReleaseTargetsView)
async def sync(
    plugin_id: int, body: MarketReleaseSyncRequest, user: AdminUser, request: Request
) -> MarketReleaseTargetsView:
    try:
        snapshot = await save_release_sync(plugin_id, user, body.preview_id)
    except (ReleaseListingMissing, GitHubPlanError) as exc:
        raise _sync_error(exc) from exc
    await record_audit_event(
        category="plugin",
        action="plugin.catalog.release.sync",
        status="success",
        user=user,
        request=request,
        details={"plugin_id": plugin_id, "tag": snapshot.tag, "release_id": snapshot.release_id},
    )
    result = await _targets(plugin_id, user)
    if result.revision != snapshot.revision:
        raise HTTPException(409, "The synchronized release changed; reopen the deployment list")
    return result


@router.post("/deploy", response_model=MarketReleaseDeployView, status_code=202)
async def deploy(
    plugin_id: int, body: MarketReleaseDeployRequest, user: AdminUser, request: Request
) -> MarketReleaseDeployView:
    server_ids = list(dict.fromkeys(body.server_ids))
    try:
        snapshot, rows = await release_targets(plugin_id, user, server_ids)
    except (ReleaseListingMissing, GitHubPlanError) as exc:
        raise _sync_error(exc) from exc
    if snapshot.revision != body.revision:
        raise HTTPException(409, "The synchronized release changed; reopen the deployment list")

    key = f"market-release-deploy:{user.id}:{plugin_id}:{body.revision}:{body.request_id}"
    token = uuid4().hex
    acquired = await redis_manager.acquire_lock(f"{key}:lock", token, expire=120)
    if acquired is None:
        raise HTTPException(503, "Deployment submission is temporarily unavailable")
    if not acquired:
        raise HTTPException(409, "This deployment request is already being submitted")
    try:
        cached = await redis_manager.get(key) or {"server_ids": server_ids, "items": {}}
        if cached["server_ids"] != server_ids:
            raise HTTPException(409, "This request ID was used for a different server selection")
        eligible = {row["server_id"]: row for row in rows}
        results = []
        for server_id in server_ids:
            target = eligible.get(server_id)
            if target is None:
                item = MarketReleaseDeployItemView(
                    server_id=server_id, error="No eligible tracked installation"
                )
            elif str(server_id) in cached["items"]:
                item = MarketReleaseDeployItemView.model_validate(cached["items"][str(server_id)])
            else:
                item = await _enqueue_target(user.id, snapshot, target)
                cached["items"][str(server_id)] = item.model_dump(mode="json")
                await redis_manager.set(key, cached, expire=900)
            results.append(item)
    finally:
        await redis_manager.release_lock(f"{key}:lock", token)
    await record_audit_event(
        category="plugin",
        action="plugin.catalog.release.deploy",
        status="requested",
        user=user,
        request=request,
        details={
            "plugin_id": plugin_id,
            "tag": snapshot.tag,
            "operation_ids": [item.operation.operation_id for item in results if item.operation],
        },
    )
    return MarketReleaseDeployView(items=results)


async def _enqueue_target(actor_user_id, snapshot, target) -> MarketReleaseDeployItemView:
    server_id = target["server_id"]
    server_name = target["server_name"]
    try:
        await reject_stuck_lock_unless_active(server_id)
        record = await enqueue_plugin_auto_update(
            server_id=server_id,
            actor_user_id=actor_user_id,
            plugin_id=target["managed_plugin_id"],
            pinned=snapshot,
        )
    except (HTTPException, ServerOperationConflict) as exc:
        return MarketReleaseDeployItemView(
            server_id=server_id,
            server_name=server_name,
            error=str(exc.detail) if isinstance(exc, HTTPException) else str(exc),
        )
    return MarketReleaseDeployItemView(
        server_id=server_id, server_name=server_name, operation=to_view(record)
    )
