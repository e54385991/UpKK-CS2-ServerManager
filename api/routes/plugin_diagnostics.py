"""Permission-checked plugin crash diagnostic endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from api.dependencies import ActiveUser, DatabaseSession
from modules import (
    PluginDiagnosticExecuteRequest,
    PluginDiagnosticPlanRequest,
    PluginDiagnosticPlanResponse,
    PluginDiagnosticRunResponse,
)
from services.ai_access import AgentAccessDenied, enforce_agent_rate_limit
from services.plugin_diagnostic_service import (
    build_diagnostic_plan,
    execute_diagnostic_plan,
    get_diagnostic_recommendation,
    get_diagnostic_run,
    restore_diagnostic_run,
)

router = APIRouter(
    prefix="/api/servers/{server_id}/plugin-diagnostics", tags=["plugin-diagnostics"]
)


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/recommendation")
async def read_plugin_diagnostic_recommendation(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict:
    """Return a read-only hint; this endpoint never changes plugin state."""
    try:
        return await get_diagnostic_recommendation(db, current_user, server_id)
    except AgentAccessDenied as exc:
        raise _not_found(exc) from exc


@router.post("/plan", response_model=PluginDiagnosticPlanResponse)
async def plan_plugin_diagnostic(
    server_id: int,
    request: PluginDiagnosticPlanRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict:
    try:
        await enforce_agent_rate_limit(current_user.id, "diagnostic_plan", limit=10)
        return await build_diagnostic_plan(db, current_user, server_id, request.scope)
    except AgentAccessDenied as exc:
        raise _not_found(exc) from exc


@router.post("/runs", response_model=PluginDiagnosticRunResponse)
async def run_plugin_diagnostic(
    server_id: int,
    request: PluginDiagnosticExecuteRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict:
    try:
        await enforce_agent_rate_limit(
            current_user.id, "diagnostic_execute", limit=2, window_seconds=300
        )
        return await execute_diagnostic_plan(
            db,
            current_user,
            server_id,
            request.scope,
            request.expected_plan_hash,
        )
    except AgentAccessDenied as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/runs/{diagnostic_id}", response_model=PluginDiagnosticRunResponse)
async def read_plugin_diagnostic(
    server_id: int,
    diagnostic_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict:
    try:
        return await get_diagnostic_run(db, current_user, server_id, diagnostic_id)
    except (AgentAccessDenied, LookupError) as exc:
        raise _not_found(exc) from exc


@router.post("/runs/{diagnostic_id}/restore", response_model=PluginDiagnosticRunResponse)
async def restore_plugin_diagnostic(
    server_id: int,
    diagnostic_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict:
    try:
        return await restore_diagnostic_run(db, current_user, server_id, diagnostic_id)
    except (AgentAccessDenied, LookupError) as exc:
        raise _not_found(exc) from exc


@router.post("/runs/{diagnostic_id}/resume", response_model=PluginDiagnosticRunResponse)
async def resume_plugin_diagnostic(
    server_id: int,
    diagnostic_id: str,
    request: PluginDiagnosticExecuteRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict:
    """Safely resume by restoring the interrupted snapshot and starting a newly approved run."""
    try:
        existing = await get_diagnostic_run(db, current_user, server_id, diagnostic_id)
        if existing["status"] not in {"interrupted", "failed", "inconclusive"}:
            raise ValueError("Only interrupted or incomplete diagnostics can be resumed")
        await restore_diagnostic_run(db, current_user, server_id, diagnostic_id)
        return await execute_diagnostic_plan(
            db,
            current_user,
            server_id,
            request.scope,
            request.expected_plan_hash,
        )
    except (AgentAccessDenied, LookupError) as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
