"""Market plugin preflight, install enqueue, and uninstall enqueue."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from api.dependencies import (
    ActiveUser,
    DatabaseSession,
    close_request_session,
    require_server_access,
)
from modules import MarketPlugin
from services.audit_log_service import record_audit_event
from services.plugin_conflict_service import (
    PluginPlanError,
    plan_plugin_install,
    validate_plugin_plan_acknowledgements,
)
from services.server_operation_hub import ServerOperationConflict

from .operation_locks import reject_stuck_lock_unless_active
from .operation_runner import enqueue_github_plugin_uninstall, enqueue_plugin_install
from .operations import to_view
from .schemas import (
    DEFAULT_PLUGIN_FRAMEWORK,
    GitHubUninstallRequest,
    PluginAINoticeView,
    PluginConflictView,
    PluginFrameworkCompatibilityView,
    PluginInstallPlanView,
    PluginInstallRequest,
    PluginInstallStep,
    PluginRef,
    ServerOperationView,
)

install_router = APIRouter(
    prefix="/api/v1/servers/{server_id}/plugins",
    tags=["v1-plugins"],
)


def _conflict_views(items: list[dict[str, Any]]) -> list[PluginConflictView]:
    return [
        PluginConflictView(
            rule_id=int(item["rule_id"]),
            plugin_a_id=int(item["plugin_a_id"]),
            plugin_b_id=int(item["plugin_b_id"]),
            severity=str(item["severity"]),
            reason=str(item["reason"]),
        )
        for item in items
    ]


def _framework_view(compatibility: dict[str, Any]) -> PluginFrameworkCompatibilityView:
    return PluginFrameworkCompatibilityView(
        plugin=str(compatibility.get("plugin") or DEFAULT_PLUGIN_FRAMEWORK),
        installed=[str(item) for item in compatibility.get("installed") or []],
        conflicting=[str(item) for item in compatibility.get("conflicting") or []],
        missing=bool(compatibility.get("missing")),
        mismatch=bool(compatibility.get("mismatch")),
    )


def _ai_notice_views(notices: list[dict[str, Any]]) -> list[PluginAINoticeView]:
    return [
        PluginAINoticeView(
            plugin_id=int(item["plugin_id"]),
            title=str(item["title"]),
            reviewed=bool(item.get("reviewed")),
            requirements=[str(value) for value in item.get("requirements") or []],
            notes=[str(value) for value in item.get("notes") or []],
        )
        for item in notices
    ]


def to_plan_view(plan: dict[str, Any]) -> PluginInstallPlanView:
    plugin = plan["plugin"]
    return PluginInstallPlanView(
        server_id=int(plan["server_id"]),
        plugin=PluginRef(id=int(plugin["id"]), title=str(plugin["title"])),
        dependencies=[
            PluginRef(id=int(item["id"]), title=str(item["title"]))
            for item in plan.get("dependencies") or []
        ],
        installation_order=[int(item) for item in plan.get("installation_order") or []],
        already_installed=[int(item) for item in plan.get("already_installed") or []],
        tracking_records_without_remote_evidence=list(
            plan.get("tracking_records_without_remote_evidence") or []
        ),
        compatibility_unknown=list(plan.get("compatibility_unknown") or []),
        hard_conflicts=_conflict_views(list(plan.get("hard_conflicts") or [])),
        warnings=_conflict_views(list(plan.get("warnings") or [])),
        framework=_framework_view(plan.get("framework") or {}),
        ai_unreviewed=plan.get("ai_unreviewed", []),
        ai_notices=_ai_notice_views(list(plan.get("ai_notices") or [])),
        steps=[
            PluginInstallStep(
                order=int(step["order"]),
                plugin_id=int(step["plugin_id"]),
                title=str(step["title"]),
                kind=str(step["kind"]),
                status=str(step["status"]),
                reason=str(step["reason"]),
            )
            for step in plan.get("steps") or []
        ],
        blocked=bool(plan.get("blocked")),
        plan_hash=str(plan["plan_hash"]),
    )


async def _require_existing_plugin(db: DatabaseSession, plugin_id: int) -> MarketPlugin:
    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if plugin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    return plugin


@install_router.get(
    "/market/{plugin_id}/preflight",
    response_model=PluginInstallPlanView,
)
async def plugin_install_preflight(
    server_id: int,
    plugin_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
    install_dependencies: bool = Query(
        False,
        description="Whether to include declared dependencies in the plan (opt-in, matches the web installer)",
    ),
) -> PluginInstallPlanView:
    """Resolve dependencies and conflicts without changing the server."""
    await require_server_access(db, server_id, current_user)
    await _require_existing_plugin(db, plugin_id)
    await close_request_session(db)
    try:
        plan = await plan_plugin_install(
            server_id,
            plugin_id,
            include_dependencies=install_dependencies,
        )
    except PluginPlanError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return to_plan_view(plan)


@install_router.post(
    "/market/{plugin_id}/install",
    response_model=ServerOperationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def install_market_plugin(
    server_id: int,
    plugin_id: int,
    body: PluginInstallRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    """Accept a market install and return immediately with an operation_id."""
    await require_server_access(db, server_id, current_user)
    await _require_existing_plugin(db, plugin_id)
    await reject_stuck_lock_unless_active(server_id)
    await close_request_session(db)

    try:
        plan = await plan_plugin_install(
            server_id,
            plugin_id,
            include_dependencies=body.install_dependencies,
        )
        validate_plugin_plan_acknowledgements(
            plan,
            body.acknowledge_warning_rule_ids,
            acknowledge_framework_mismatch=body.acknowledge_framework_mismatch,
            acknowledge_ai_unreviewed=body.acknowledge_ai_unreviewed,
        )
    except PluginPlanError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if plan.get("blocked"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Installation blocked by a hard conflict rule",
        )
    if body.plan_hash and plan.get("plan_hash") != body.plan_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Plugin plan changed; review and approve the new plan",
        )

    try:
        record = await enqueue_plugin_install(
            server_id=server_id,
            plugin_id=plugin_id,
            actor_user_id=current_user.id,
            acknowledge_warning_rule_ids=body.acknowledge_warning_rule_ids,
            acknowledge_framework_mismatch=body.acknowledge_framework_mismatch,
            acknowledge_ai_unreviewed=body.acknowledge_ai_unreviewed,
            plan_hash=body.plan_hash or plan["plan_hash"],
            download_url=body.download_url,
            upgrade_mode=body.upgrade_mode,
            install_dependencies=body.install_dependencies,
            exclude_dirs=list(body.exclude_dirs),
            exclude_files=list(body.exclude_files),
        )
    except ServerOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    await record_audit_event(
        category="plugin",
        action="plugin.install",
        status="requested",
        user=current_user,
        request=request,
        server_id=server_id,
        details={
            "operation_id": record["operation_id"],
            "plugin_id": plugin_id,
            "upgrade_mode": body.upgrade_mode,
            "framework_mismatch_acknowledged": body.acknowledge_framework_mismatch,
        },
    )
    return to_view(record)


@install_router.post(
    "/market/{plugin_id}/uninstall",
    response_model=ServerOperationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def uninstall_market_plugin(
    server_id: int,
    plugin_id: int,
    body: GitHubUninstallRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    """Uninstall a market plugin by deleting selected files, then drop tracking."""
    await require_server_access(db, server_id, current_user)
    await _require_existing_plugin(db, plugin_id)
    await reject_stuck_lock_unless_active(server_id)
    await close_request_session(db)
    try:
        record = await enqueue_github_plugin_uninstall(
            server_id=server_id,
            actor_user_id=current_user.id,
            files_to_delete=list(body.files_to_delete),
            market_plugin_id=plugin_id,
        )
    except ServerOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    await record_audit_event(
        category="plugin",
        action="plugin.uninstall",
        status="requested",
        user=current_user,
        request=request,
        server_id=server_id,
        details={
            "operation_id": record["operation_id"],
            "plugin_id": plugin_id,
            "file_count": len(body.files_to_delete),
        },
    )
    return to_view(record)
