"""Versioned one-click game-mode install: catalog, preflight, 202 enqueue."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from api.dependencies import ActiveUser, DatabaseSession, require_server_access
from services.game_mode_install_service import (
    GameModePlanError,
    build_game_mode_plan,
    catalog_for_server,
)
from services.game_mode_recipes import UnknownGameModeError, get_recipe
from services.game_mode_remote import GameModeRemoteError
from services.plugin_conflict_service import PluginPlanError, validate_plugin_plan_acknowledgements
from services.server_operation_hub import ServerOperationConflict

from .operation_locks import reject_stuck_lock_unless_active
from .operation_runner import enqueue_game_mode_install
from .operations import to_view
from .plugins import to_plan_view
from .schemas import (
    GameModeCatalogView,
    GameModeInstallRequest,
    GameModeMapView,
    GameModeMutationView,
    GameModePlanView,
    GameModePreflightRequest,
    GameModeStartupView,
    GameModeStepView,
    GameModeSummaryView,
    PluginConflictView,
    ServerOperationView,
)

router = APIRouter(
    prefix="/api/v1/servers/{server_id}/game-modes",
    tags=["v1-game-modes"],
)


def _conflicts(items: list[dict[str, Any]]) -> list[PluginConflictView]:
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


def _step_view(step: dict[str, Any]) -> GameModeStepView:
    return GameModeStepView(
        id=str(step["id"]),
        action=str(step["action"]),
        status=str(step["status"]),
        destructive=bool(step.get("destructive", False)),
        path=step.get("path"),
        title=step.get("title"),
        plugin_id=step.get("plugin_id"),
        framework=step.get("framework"),
        name=step.get("name"),
        workshop_id=step.get("workshop_id"),
        values=step.get("values"),
        files=step.get("files"),
    )


def to_plan_http(plan: dict[str, Any]) -> GameModePlanView:
    plugin_plans = {
        title: to_plan_view(raw) for title, raw in (plan.get("plugin_plans") or {}).items() if raw
    }
    return GameModePlanView(
        server_id=int(plan["server_id"]),
        mode_id=str(plan["mode_id"]),
        wipe_addons=bool(plan["wipe_addons"]),
        addons_path=str(plan["addons_path"]),
        current=dict(plan.get("current") or {}),
        startup=GameModeStartupView(
            before=plan["startup"].get("before"),
            after=plan["startup"].get("after"),
            changed=bool(plan["startup"].get("changed")),
        ),
        plugin_config=dict(plan.get("plugin_config") or {}),
        maps=[
            GameModeMapView(name=str(item["name"]), workshop_id=str(item["workshop_id"]))
            for item in plan.get("maps") or []
        ],
        wait_files=list(plan.get("wait_files") or []),
        plugin_plans=plugin_plans,
        hard_conflicts=_conflicts(list(plan.get("hard_conflicts") or [])),
        warnings=_conflicts(list(plan.get("warnings") or [])),
        steps=[_step_view(step) for step in plan.get("steps") or []],
        mutations=[
            GameModeMutationView(
                id=str(item["id"]),
                target=str(item["target"]),
                before=item.get("before"),
                after=item.get("after"),
                destructive=bool(item.get("destructive", False)),
                status=str(item.get("status") or "pending"),
            )
            for item in plan.get("mutations") or []
        ],
        blocked=bool(plan.get("blocked")),
        blocking_reasons=list(plan.get("blocking_reasons") or []),
        plan_hash=str(plan["plan_hash"]),
    )


@router.get("", response_model=GameModeCatalogView)
async def list_game_modes(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> GameModeCatalogView:
    """List installable game-mode recipes and a best-effort presence snapshot."""
    server = await require_server_access(db, server_id, current_user)
    try:
        payload = await catalog_for_server(db, server)
    except (GameModePlanError, GameModeRemoteError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return GameModeCatalogView(
        server_id=int(payload["server_id"]),
        reachable=bool(payload["reachable"]),
        additional_parameters=payload.get("additional_parameters"),
        addons_path=str(payload["addons_path"]),
        addons_present=payload.get("addons_present"),
        swiftly_installed=payload.get("swiftly_installed"),
        modes=[
            GameModeSummaryView(
                id=str(item["id"]),
                launch_upsert=dict(item["launch_upsert"]),
                frameworks=list(item["frameworks"]),
                market_plugin_titles=list(item["market_plugin_titles"]),
                maps=[
                    GameModeMapView(name=str(entry["name"]), workshop_id=str(entry["workshop_id"]))
                    for entry in item["maps"]
                ],
                plugin_config=dict(item["plugin_config"]),
                startup_workshop_map=str(item["startup_workshop_map"]),
                present=dict(item["present"]),
                missing_market_plugins=list(item.get("missing_market_plugins") or []),
            )
            for item in payload["modes"]
        ],
    )


@router.post("/{mode_id}/preflight", response_model=GameModePlanView)
async def game_mode_preflight(
    server_id: int,
    mode_id: str,
    body: GameModePreflightRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> GameModePlanView:
    """Inspect the server and return every mutation the install would make."""
    server = await require_server_access(db, server_id, current_user)
    try:
        get_recipe(mode_id)
    except UnknownGameModeError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown game mode"
        ) from exc
    try:
        plan = await build_game_mode_plan(db, server, mode_id, wipe_addons=body.wipe_addons)
    except (GameModePlanError, GameModeRemoteError, PluginPlanError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return to_plan_http(plan)


@router.post(
    "/{mode_id}/install",
    response_model=ServerOperationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def install_game_mode(
    server_id: int,
    mode_id: str,
    body: GameModeInstallRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    """Accept a confirmed game-mode plan and return immediately with an operation_id."""
    server = await require_server_access(db, server_id, current_user)
    try:
        get_recipe(mode_id)
    except UnknownGameModeError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown game mode"
        ) from exc
    if body.wipe_addons and not body.wipe_addons_acknowledged:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Wiping addons requires an explicit acknowledgement",
        )

    await reject_stuck_lock_unless_active(server_id)
    try:
        plan = await build_game_mode_plan(db, server, mode_id, wipe_addons=body.wipe_addons)
        for plugin_plan in (plan.get("plugin_plans") or {}).values():
            validate_plugin_plan_acknowledgements(plugin_plan, body.acknowledge_warning_rule_ids)
    except (GameModePlanError, GameModeRemoteError, PluginPlanError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if plan.get("blocked"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="; ".join(plan.get("blocking_reasons") or ["Game-mode install is blocked"]),
        )
    if plan.get("plan_hash") != body.plan_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Game-mode plan changed; review and approve the new plan",
        )

    try:
        record = await enqueue_game_mode_install(
            server_id=server_id,
            mode_id=mode_id,
            actor_user_id=current_user.id,
            wipe_addons=body.wipe_addons,
            wipe_addons_acknowledged=body.wipe_addons_acknowledged,
            plan_hash=body.plan_hash,
            acknowledge_warning_rule_ids=list(body.acknowledge_warning_rule_ids),
        )
    except ServerOperationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return to_view(record)
