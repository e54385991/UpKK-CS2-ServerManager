"""Game Mode operation workers."""

from __future__ import annotations

from fastapi import HTTPException

from api.dependencies import require_server_access
from modules import User
from modules.database import async_session_maker
from services.game_mode_install_service import GameModePlanError, execute_game_mode_plan
from services.maintenance_lock import OperationBusyError
from services.plugin_conflict_service import PluginPlanError
from services.server_operation_hub import (
    ServerOperationConflict,
    server_operation_hub,
)

from .shared import _dispatch, logger


async def enqueue_game_mode_install(
    *,
    server_id: int,
    mode_id: str,
    actor_user_id: int,
    wipe_addons: bool,
    wipe_addons_acknowledged: bool,
    plan_hash: str,
    acknowledge_warning_rule_ids: list[int],
) -> dict:
    """Create an operation record and install a game-mode recipe in the background."""
    del wipe_addons_acknowledged
    command = f"game-mode install {mode_id}"
    if wipe_addons:
        command += " --wipe-addons"
    record = await server_operation_hub.create(
        server_id=server_id,
        action="install_game_mode",
        actor_user_id=actor_user_id,
        command=command,
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_game_mode_install(
            operation_id=operation_id,
            mode_id=mode_id,
            wipe_addons=wipe_addons,
            plan_hash=plan_hash,
            acknowledge_warning_rule_ids=list(acknowledge_warning_rule_ids),
        ),
    )


async def run_game_mode_install(
    *,
    operation_id: str,
    mode_id: str,
    wipe_addons: bool,
    plan_hash: str,
    acknowledge_warning_rule_ids: list[int],
) -> None:
    """Execute one queued game-mode install through the recipe planner."""
    record = await server_operation_hub.get(operation_id)
    if record is None:
        return

    await server_operation_hub.mark_running(operation_id)
    server_id = int(record["server_id"])
    actor_user_id = int(record["actor_user_id"])

    async def progress(message: str, _kind: str = "status") -> None:
        await server_operation_hub.emit(
            operation_id,
            "progress",
            kind="output",
            message=message,
        )

    try:
        async with async_session_maker() as db:
            user = await db.get(User, actor_user_id)
            if user is None or not user.is_active:
                await server_operation_hub.finish(
                    operation_id,
                    success=False,
                    message="The operator account is no longer available",
                )
                return

            server = await require_server_access(db, server_id, user)
            result = await execute_game_mode_plan(
                db,
                server,
                user,
                mode_id,
                wipe_addons=wipe_addons,
                expected_plan_hash=plan_hash,
                acknowledged_warning_rule_ids=acknowledge_warning_rule_ids,
                progress=progress,
                operation_id=operation_id,
            )
            await server_operation_hub.finish(
                operation_id,
                success=bool(result.get("success")),
                message=str(result.get("message") or "Game-mode installation finished"),
            )
    except ServerOperationConflict as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except OperationBusyError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except (GameModePlanError, PluginPlanError) as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await server_operation_hub.finish(operation_id, success=False, message=detail)
    except Exception:
        logger.exception("Background game-mode install %s failed", operation_id)
        await server_operation_hub.finish(
            operation_id,
            success=False,
            message="The game-mode install failed unexpectedly",
        )
