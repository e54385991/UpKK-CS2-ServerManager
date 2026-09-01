"""Cleanup operation workers."""

from __future__ import annotations

from fastapi import HTTPException

from api.dependencies import require_server_access
from modules import User
from modules.database import async_session_maker
from services.game_cleanup_service import game_cleanup_service
from services.maintenance_lock import OperationBusyError, maintenance_lock_service
from services.server_operation_hub import (
    ServerOperationConflict,
    server_operation_hub,
)
from services.ssh_manager import SSHManager
from services.system_cleanup_service import normalize_targets, system_cleanup_service

from .shared import _audit_terminal, _dispatch, _progress_emitter, logger


async def enqueue_cleanup_delete(
    *,
    server_id: int,
    actor_user_id: int,
    mode: str,
    paths: list[str],
    confirmation_text: str | None,
) -> dict:
    """Queue a game-directory cleanup delete on the per-server FIFO."""
    record = await server_operation_hub.create(
        server_id=server_id,
        action="cleanup_delete",
        actor_user_id=actor_user_id,
        command=f"cleanup delete --mode {mode} --items {len(paths)}",
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_cleanup_delete(
            operation_id=operation_id,
            mode=mode,
            paths=list(paths),
            confirmation_text=confirmation_text,
        ),
    )


async def run_cleanup_delete(
    *,
    operation_id: str,
    mode: str,
    paths: list[str],
    confirmation_text: str | None,
) -> None:
    """Delete queued cleanup candidates over SSH."""
    record = await server_operation_hub.get(operation_id)
    if record is None:
        return

    await server_operation_hub.mark_running(operation_id)
    server_id = int(record["server_id"])
    actor_user_id = int(record["actor_user_id"])
    progress = _progress_emitter(operation_id)
    manager = SSHManager()

    async def fail(message: str) -> None:
        await server_operation_hub.finish(operation_id, success=False, message=message)
        await _audit_terminal(
            record,
            category="files",
            action="files.cleanup",
            success=False,
            message=message,
            extra={"mode": mode, "path_count": len(paths)},
        )

    try:
        async with async_session_maker() as db:
            user = await db.get(User, actor_user_id)
            if user is None or not user.is_active:
                await fail("The operator account is no longer available")
                return

            server = await require_server_access(db, server_id, user)
            async with maintenance_lock_service.get(
                server_id,
                operation="cleanup_delete",
                wait=False,
                ttl=7200,
            ):
                await progress(f"Deleting cleanup items ({mode})")
                success, result, error = await game_cleanup_service.delete(
                    manager,
                    server,
                    mode,
                    paths=paths,
                    confirmation_text=confirmation_text,
                )
            if error:
                await fail(error)
                return
            message = str(result.get("message") or "Cleanup finished")
            await server_operation_hub.finish(operation_id, success=success, message=message)
            await _audit_terminal(
                record,
                category="files",
                action="files.cleanup",
                success=success,
                message=message,
                extra={
                    "mode": mode,
                    "path_count": len(paths),
                    "deleted_count": int(result.get("deleted_count") or 0),
                },
            )
    except ServerOperationConflict as exc:
        await fail(str(exc))
    except OperationBusyError as exc:
        await fail(str(exc))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await fail(detail)
    except Exception:
        logger.exception("Background cleanup delete %s failed", operation_id)
        await fail("Cleanup delete failed unexpectedly")
    finally:
        try:
            await manager.disconnect()
        except Exception:
            pass


async def enqueue_cleanup_system(
    *,
    server_id: int,
    actor_user_id: int,
    targets: list[str],
    retain_days: int | None,
) -> dict:
    """Queue a privileged system cleanup apply on the per-server FIFO."""
    record = await server_operation_hub.create(
        server_id=server_id,
        action="cleanup_system",
        actor_user_id=actor_user_id,
        command=f"cleanup system --targets {','.join(targets)}",
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_cleanup_system(
            operation_id=operation_id,
            targets=list(targets),
            retain_days=retain_days,
        ),
    )


async def run_cleanup_system(
    *,
    operation_id: str,
    targets: list[str],
    retain_days: int | None,
) -> None:
    """Apply queued system cleanup targets over SSH."""
    record = await server_operation_hub.get(operation_id)
    if record is None:
        return

    await server_operation_hub.mark_running(operation_id)
    server_id = int(record["server_id"])
    actor_user_id = int(record["actor_user_id"])
    progress = _progress_emitter(operation_id)
    manager = SSHManager()

    async def fail(message: str) -> None:
        await server_operation_hub.finish(operation_id, success=False, message=message)
        await _audit_terminal(
            record,
            category="files",
            action="files.cleanup_system",
            success=False,
            message=message,
            extra={"targets": list(targets)},
        )

    try:
        async with async_session_maker() as db:
            user = await db.get(User, actor_user_id)
            if user is None or not user.is_active:
                await fail("The operator account is no longer available")
                return

            server = await require_server_access(db, server_id, user)
            selected = normalize_targets(targets)
            async with maintenance_lock_service.get(
                server_id,
                operation="cleanup_system",
                wait=False,
                ttl=7200,
            ):
                await progress(f"Applying system cleanup ({', '.join(selected)})")
                payload = await system_cleanup_service.apply(
                    manager,
                    server,
                    selected,
                    retain_days=retain_days,
                )
            success = bool(payload.get("success"))
            message = str(payload.get("message") or "System cleanup finished")
            await server_operation_hub.finish(operation_id, success=success, message=message)
            await _audit_terminal(
                record,
                category="files",
                action="files.cleanup_system",
                success=success,
                message=message,
                extra={
                    "targets": list(selected),
                    "applied": list(payload.get("applied") or []),
                    "deleted_count": int(payload.get("deleted_count") or 0),
                },
            )
    except ServerOperationConflict as exc:
        await fail(str(exc))
    except OperationBusyError as exc:
        await fail(str(exc))
    except ValueError as exc:
        await fail(str(exc))
    except RuntimeError as exc:
        await fail(str(exc))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await fail(detail)
    except Exception:
        logger.exception("Background system cleanup %s failed", operation_id)
        await fail("System cleanup failed unexpectedly")
    finally:
        try:
            await manager.disconnect()
        except Exception:
            pass
