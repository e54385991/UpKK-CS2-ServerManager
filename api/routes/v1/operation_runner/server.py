"""Server operation workers."""

from __future__ import annotations

from fastapi import HTTPException

from api.routes.actions.deployment import execute_server_action
from modules import User
from modules.database import async_session_maker
from modules.schemas.common import ALLOWED_SERVER_ACTIONS
from modules.schemas.servers import ServerAction
from services.maintenance_lock import OperationBusyError, maintenance_lock_service
from services.operations.types import OperationCommand
from services.server_operation_hub import (
    ServerOperationConflict,
    server_operation_hub,
)

from .shared import _dispatch, logger


async def enqueue_server_operation(
    *,
    server_id: int,
    action: str,
    actor_user_id: int,
) -> dict:
    """Create an operation record and run the action in the background."""
    command = OperationCommand(
        server_id=server_id,
        action=action,
        actor_user_id=actor_user_id,
    )
    if command.action not in ALLOWED_SERVER_ACTIONS:
        raise HTTPException(status_code=422, detail=f"Invalid action: {action}")

    record = await server_operation_hub.create(
        server_id=command.server_id,
        action=command.action,
        actor_user_id=command.actor_user_id,
        command=f"server {command.action}",
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_server_operation(operation_id=operation_id),
    )


async def run_server_operation(
    *,
    operation_id: str,
) -> None:
    """Execute one queued operation using the legacy server_action implementation."""
    record = await server_operation_hub.get(operation_id)
    if record is None:
        return

    await server_operation_hub.mark_running(operation_id)
    server_id = int(record["server_id"])
    action = str(record["action"])
    actor_user_id = int(record["actor_user_id"])

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

            async with maintenance_lock_service.get(
                server_id,
                operation="server_action",
                wait=False,
                ttl=7200,
            ):
                result = await execute_server_action(
                    server_id,
                    ServerAction(action=action),
                    db,
                    user,
                    None,
                    None,
                )
            server_status = None
            if isinstance(result.data, dict):
                server_status = result.data.get("status")
            await server_operation_hub.finish(
                operation_id,
                success=bool(result.success),
                message=result.message,
                server_status=str(server_status) if server_status else None,
            )
    except ServerOperationConflict as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except OperationBusyError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await server_operation_hub.finish(operation_id, success=False, message=detail)
    except Exception:
        logger.exception("Background server operation %s failed", operation_id)
        await server_operation_hub.finish(
            operation_id,
            success=False,
            message="The operation failed unexpectedly",
        )
