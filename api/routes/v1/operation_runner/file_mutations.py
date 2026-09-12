"""Queued file move and batch deletion workers."""

from __future__ import annotations

from fastapi import HTTPException

from api.dependencies import require_server_access
from modules import User
from modules.database import async_session_maker
from services.maintenance_lock import OperationBusyError, maintenance_lock_service
from services.server_operation_hub import ServerOperationConflict, server_operation_hub
from services.ssh_manager import SSHManager

from .shared import _audit_terminal, _dispatch, _progress_emitter, logger


async def enqueue_batch_delete(*, server_id: int, actor_user_id: int, paths: list[str]) -> dict:
    record = await server_operation_hub.create(
        server_id=server_id,
        action="batch_delete",
        actor_user_id=actor_user_id,
        command=f"rm -rf -- ({len(paths)} paths)",
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_batch_delete(operation_id=operation_id, paths=list(paths)),
    )


async def run_batch_delete(*, operation_id: str, paths: list[str]) -> None:
    record = await server_operation_hub.get(operation_id)
    if record is None:
        return
    await server_operation_hub.mark_running(operation_id)
    progress = _progress_emitter(operation_id)
    manager = SSHManager()
    message = "Batch delete failed unexpectedly"
    success = False
    try:
        async with async_session_maker() as db:
            user = await db.get(User, int(record["actor_user_id"]))
            if user is None or not user.is_active:
                message = "The operator account is no longer available"
            else:
                server = await require_server_access(db, int(record["server_id"]), user)
                await db.commit()
                async with maintenance_lock_service.get(
                    int(record["server_id"]), operation="batch_delete", wait=False, ttl=7200
                ):
                    await progress(f"Deleting {len(paths)} selected item(s)")
                    success, message = await manager.delete_paths(paths, server)
        await server_operation_hub.finish(operation_id, success=success, message=message)
        await _audit_terminal(
            record,
            category="files",
            action="files.batch_delete",
            success=success,
            message=message,
            extra={"path_count": len(paths)},
        )
    except (ServerOperationConflict, OperationBusyError, HTTPException) as exc:
        message = (
            exc.detail
            if isinstance(exc, HTTPException) and isinstance(exc.detail, str)
            else str(exc)
        )
        await server_operation_hub.finish(operation_id, success=False, message=message)
        await _audit_terminal(
            record, category="files", action="files.batch_delete", success=False, message=message
        )
    except Exception:
        logger.exception("Background batch delete %s failed", operation_id)
        await server_operation_hub.finish(operation_id, success=False, message=message)
        await _audit_terminal(
            record, category="files", action="files.batch_delete", success=False, message=message
        )
    finally:
        try:
            await manager.disconnect()
        except Exception:
            logger.debug("SSH disconnect failed after batch delete", exc_info=True)


async def enqueue_move_paths(
    *, server_id: int, actor_user_id: int, sources: list[str], destination: str, conflict: str
) -> dict:
    record = await server_operation_hub.create(
        server_id=server_id,
        action="move_paths",
        actor_user_id=actor_user_id,
        command=f"move {len(sources)} path(s) -> {destination} ({conflict})",
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_move_paths(
            operation_id=operation_id,
            sources=list(sources),
            destination=destination,
            conflict=conflict,
        ),
    )


async def run_move_paths(
    *, operation_id: str, sources: list[str], destination: str, conflict: str
) -> None:
    record = await server_operation_hub.get(operation_id)
    if record is None:
        return
    await server_operation_hub.mark_running(operation_id)
    manager = SSHManager()
    progress = _progress_emitter(operation_id)
    success = False
    message = "Move failed unexpectedly"
    try:
        async with async_session_maker() as db:
            user = await db.get(User, int(record["actor_user_id"]))
            if user is None or not user.is_active:
                message = "The operator account is no longer available"
            else:
                server = await require_server_access(db, int(record["server_id"]), user)
                await db.commit()
                async with maintenance_lock_service.get(
                    int(record["server_id"]), operation="move_paths", wait=False, ttl=7200
                ):
                    await progress(f"Moving {len(sources)} selected item(s)")
                    success, message = await manager.move_paths(
                        sources, destination, server, conflict=conflict
                    )
        await server_operation_hub.finish(operation_id, success=success, message=message)
        await _audit_terminal(
            record,
            category="files",
            action="files.move",
            success=success,
            message=message,
            extra={"source_count": len(sources), "destination": destination, "conflict": conflict},
        )
    except (ServerOperationConflict, OperationBusyError, HTTPException) as exc:
        message = (
            exc.detail
            if isinstance(exc, HTTPException) and isinstance(exc.detail, str)
            else str(exc)
        )
        await server_operation_hub.finish(operation_id, success=False, message=message)
        await _audit_terminal(
            record, category="files", action="files.move", success=False, message=message
        )
    except Exception:
        logger.exception("Background move %s failed", operation_id)
        await server_operation_hub.finish(operation_id, success=False, message=message)
        await _audit_terminal(
            record, category="files", action="files.move", success=False, message=message
        )
    finally:
        try:
            await manager.disconnect()
        except Exception:
            logger.debug("SSH disconnect failed after move", exc_info=True)
