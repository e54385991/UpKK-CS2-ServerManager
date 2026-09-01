"""Versioned plugin-crash diagnostics for the Next monitoring workspace."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from api.dependencies import ActiveUser, DatabaseSession
from services.ai_access import AgentAccessDenied, enforce_agent_rate_limit
from services.audit_log_service import record_audit_event
from services.plugin_diagnostic_service import (
    build_diagnostic_plan,
    get_diagnostic_recommendation,
    get_diagnostic_run,
    get_latest_diagnostic_run,
)
from services.server_operation_hub import ServerOperationConflict

from .operation_locks import reject_stuck_lock_unless_active
from .operation_runner import (
    enqueue_plugin_diagnostic_execute,
    enqueue_plugin_diagnostic_restore,
    enqueue_plugin_diagnostic_resume,
)
from .operations import to_view
from .schemas import (
    PluginDiagnosticExecuteBody,
    PluginDiagnosticPlanBody,
    PluginDiagnosticPlanView,
    PluginDiagnosticRecommendationView,
    PluginDiagnosticRunView,
    ServerOperationView,
)

router = APIRouter(
    prefix="/api/v1/servers/{server_id}/plugin-diagnostics",
    tags=["v1-plugin-diagnostics"],
)


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _to_recommendation(raw: dict) -> PluginDiagnosticRecommendationView:
    return PluginDiagnosticRecommendationView(
        recommended=bool(raw.get("recommended")),
        reason=raw.get("reason"),
        recently_updated=bool(raw.get("recently_updated")),
        last_update_time=raw.get("last_update_time"),
        restart_count=int(raw.get("restart_count") or 0),
        max_restarts=int(raw.get("max_restarts") or 0),
        window_minutes=int(raw.get("window_minutes") or 30),
    )


def _to_plan(raw: dict) -> PluginDiagnosticPlanView:
    return PluginDiagnosticPlanView(
        server_id=int(raw["server_id"]),
        scope=str(raw.get("scope") or "both"),
        plan_hash=str(raw.get("plan_hash") or ""),
        candidates=list(raw.get("candidates") or []),
        candidate_groups=list(raw.get("candidate_groups") or []),
        estimated_max_starts=int(raw.get("estimated_max_starts") or 0),
        health_policy=dict(raw.get("health_policy") or {}),
        warnings=list(raw.get("warnings") or []),
    )


def _to_run(raw: dict) -> PluginDiagnosticRunView:
    return PluginDiagnosticRunView(
        id=str(raw["id"]),
        server_id=int(raw["server_id"]),
        requested_by=int(raw["requested_by"]),
        scope=str(raw.get("scope") or "both"),
        status=str(raw.get("status") or ""),
        plan_hash=str(raw.get("plan_hash") or ""),
        culprit_keys=list(raw.get("culprit_keys") or []),
        start_attempts=int(raw.get("start_attempts") or 0),
        error=raw.get("error"),
        steps=list(raw.get("steps") or []),
        quarantine=list(raw.get("quarantine") or []),
        created_at=raw.get("created_at"),
        completed_at=raw.get("completed_at"),
    )


@router.get("/recommendation", response_model=PluginDiagnosticRecommendationView)
async def read_recommendation(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> PluginDiagnosticRecommendationView:
    try:
        return _to_recommendation(await get_diagnostic_recommendation(db, current_user, server_id))
    except AgentAccessDenied as exc:
        raise _not_found(exc) from exc


@router.post("/plan", response_model=PluginDiagnosticPlanView)
async def plan_diagnostic(
    server_id: int,
    body: PluginDiagnosticPlanBody,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> PluginDiagnosticPlanView:
    try:
        await enforce_agent_rate_limit(current_user.id, "diagnostic_plan", limit=10)
        return _to_plan(await build_diagnostic_plan(db, current_user, server_id, body.scope))
    except AgentAccessDenied as exc:
        raise _not_found(exc) from exc


@router.post("/runs", response_model=ServerOperationView, status_code=status.HTTP_202_ACCEPTED)
async def execute_diagnostic(
    server_id: int,
    body: PluginDiagnosticExecuteBody,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    try:
        await enforce_agent_rate_limit(
            current_user.id, "diagnostic_execute", limit=2, window_seconds=300
        )
        plan = await build_diagnostic_plan(db, current_user, server_id, body.scope)
        if plan["plan_hash"] != body.expected_plan_hash:
            raise ValueError("Diagnostic plan changed; inspect and approve it again")
        await reject_stuck_lock_unless_active(server_id)
        record = await enqueue_plugin_diagnostic_execute(
            server_id=server_id,
            actor_user_id=current_user.id,
            scope=body.scope,
            expected_plan_hash=body.expected_plan_hash,
        )
    except AgentAccessDenied as exc:
        raise _not_found(exc) from exc
    except ServerOperationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await record_audit_event(
        category="plugin",
        action="plugin.diagnostic.execute",
        status="requested",
        user=current_user,
        request=request,
        server_id=server_id,
        details={"operation_id": record["operation_id"], "scope": body.scope},
    )
    return to_view(record)


@router.get("/latest-run", response_model=PluginDiagnosticRunView)
async def read_latest_diagnostic_run(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> PluginDiagnosticRunView:
    try:
        return _to_run(await get_latest_diagnostic_run(db, current_user, server_id))
    except (AgentAccessDenied, LookupError) as exc:
        raise _not_found(exc) from exc


@router.get("/runs/{diagnostic_id}", response_model=PluginDiagnosticRunView)
async def read_diagnostic_run(
    server_id: int,
    diagnostic_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> PluginDiagnosticRunView:
    try:
        return _to_run(await get_diagnostic_run(db, current_user, server_id, diagnostic_id))
    except (AgentAccessDenied, LookupError) as exc:
        raise _not_found(exc) from exc


@router.post(
    "/runs/{diagnostic_id}/restore",
    response_model=ServerOperationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def restore_diagnostic(
    server_id: int,
    diagnostic_id: str,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    try:
        await get_diagnostic_run(db, current_user, server_id, diagnostic_id)
        await reject_stuck_lock_unless_active(server_id)
        record = await enqueue_plugin_diagnostic_restore(
            server_id=server_id,
            actor_user_id=current_user.id,
            diagnostic_id=diagnostic_id,
        )
    except (AgentAccessDenied, LookupError) as exc:
        raise _not_found(exc) from exc
    except ServerOperationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await record_audit_event(
        category="plugin",
        action="plugin.diagnostic.restore",
        status="requested",
        user=current_user,
        request=request,
        server_id=server_id,
        details={"operation_id": record["operation_id"], "diagnostic_id": diagnostic_id},
    )
    return to_view(record)


@router.post(
    "/runs/{diagnostic_id}/resume",
    response_model=ServerOperationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resume_diagnostic(
    server_id: int,
    diagnostic_id: str,
    body: PluginDiagnosticExecuteBody,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    try:
        existing = await get_diagnostic_run(db, current_user, server_id, diagnostic_id)
        if existing["status"] not in {"interrupted", "failed", "inconclusive"}:
            raise ValueError("Only interrupted or incomplete diagnostics can be resumed")
        await reject_stuck_lock_unless_active(server_id)
        record = await enqueue_plugin_diagnostic_resume(
            server_id=server_id,
            actor_user_id=current_user.id,
            diagnostic_id=diagnostic_id,
            scope=body.scope,
            expected_plan_hash=body.expected_plan_hash,
        )
    except (AgentAccessDenied, LookupError) as exc:
        raise _not_found(exc) from exc
    except ServerOperationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await record_audit_event(
        category="plugin",
        action="plugin.diagnostic.resume",
        status="requested",
        user=current_user,
        request=request,
        server_id=server_id,
        details={"operation_id": record["operation_id"], "diagnostic_id": diagnostic_id},
    )
    return to_view(record)
