"""Server operation workers."""

from __future__ import annotations

from fastapi import HTTPException

from api.dependencies import require_server_access
from api.routes.actions.deployment import execute_server_action
from modules import Server, User
from modules.database import async_session_maker
from modules.schemas.common import ALLOWED_SERVER_ACTIONS
from modules.schemas.servers import ServerAction
from services.ai_security import redact_sensitive_text
from services.custom_command_service import execute_custom_commands
from services.maintenance_lock import OperationBusyError, maintenance_lock_service
from services.operations.types import OperationCommand
from services.server_compatibility import EXECSTACK_FILE_ACTIONS, execstack_operation_metadata
from services.server_operation_hub import (
    ServerOperationConflict,
    server_operation_hub,
)

from .shared import _dispatch, logger


async def enqueue_game_console_command(
    *,
    server_id: int,
    command: str,
    actor_user_id: int,
) -> dict:
    """Queue one game-console command on the server's FIFO."""
    command_text = command.strip()
    record = await server_operation_hub.create(
        server_id=server_id,
        action="send_game_command",
        actor_user_id=actor_user_id,
        command=f"game-console {redact_sensitive_text(command_text, limit=500)}",
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_game_console_command(
            operation_id=operation_id,
            command=command_text,
        ),
    )


async def run_game_console_command(*, operation_id: str, command: str) -> None:
    """Execute one queued command against the detached CS2 game session."""
    record = await server_operation_hub.get(operation_id)
    if record is None:
        return

    await server_operation_hub.mark_running(operation_id)
    server_id = int(record["server_id"])
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
            server = await require_server_access(db, server_id, user)

        async with maintenance_lock_service.get(
            server_id,
            operation="send_game_command",
            wait=False,
            ttl=7200,
        ):
            await server_operation_hub.emit(
                operation_id,
                "progress",
                kind="status",
                message=(
                    f"Executing game-console command: {redact_sensitive_text(command, limit=500)}"
                ),
            )
            result = await execute_custom_commands(server, "game_process", command)

        message = str(result.get("message") or "Game-console command finished")
        await server_operation_hub.finish(
            operation_id,
            success=bool(result.get("success")),
            message=message,
        )
    except ServerOperationConflict as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except OperationBusyError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await server_operation_hub.finish(operation_id, success=False, message=detail)
    except Exception:
        logger.exception("Background game-console command %s failed", operation_id)
        await server_operation_hub.finish(
            operation_id,
            success=False,
            message="The game-console command failed unexpectedly",
        )


async def enqueue_server_operation(
    *,
    server_id: int,
    action: str,
    actor_user_id: int,
    extra: dict | None = None,
) -> dict:
    """Create an operation record and run the action in the background."""
    command = OperationCommand(
        server_id=server_id,
        action=action,
        actor_user_id=actor_user_id,
    )
    if command.action not in ALLOWED_SERVER_ACTIONS:
        raise HTTPException(status_code=422, detail=f"Invalid action: {action}")

    if extra is None and command.action in EXECSTACK_FILE_ACTIONS:
        async with async_session_maker() as db:
            server = await db.get(Server, command.server_id)
        extra = (
            execstack_operation_metadata(server, command.action)
            if server and getattr(server, "game_directory", None)
            else None
        )

    command_summary = f"server {command.action}"
    if extra and extra.get("clear_execstack"):
        command_summary += f" (clear plugin execstack: {extra.get('clear_execstack_command', 'patchelf --clear-execstack')})"
    record = await server_operation_hub.create(
        server_id=command.server_id,
        action=command.action,
        actor_user_id=command.actor_user_id,
        command=command_summary,
        extra=extra,
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
                    clear_execstack=bool(record.get("clear_execstack"))
                    if action == "restart" or action in EXECSTACK_FILE_ACTIONS
                    else False,
                    clear_execstack_targets=record.get("clear_execstack_targets"),
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
