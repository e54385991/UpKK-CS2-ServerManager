"""Diagnostics operation workers."""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException

from modules import User
from modules.database import async_session_maker
from services.maintenance_lock import OperationBusyError
from services.server_operation_hub import (
    ServerOperationConflict,
    server_operation_hub,
)

from .shared import _audit_terminal, _dispatch, logger


def _diagnostic_progress(operation_id: str):
    async def progress(event_type: str, payload: dict | None = None) -> None:
        data = payload if isinstance(payload, dict) else {}
        message = str(data.get("message") or event_type)
        await server_operation_hub.emit(
            operation_id,
            "progress",
            kind="output",
            message=message,
        )

    return progress


def _diagnostic_success(payload: dict) -> tuple[bool, str]:
    status = str(payload.get("status") or "")
    error = str(payload.get("error") or "").strip()
    success = status not in {"failed", "interrupted"}
    message = error or status or "Plugin diagnostic finished"
    return success, message


async def enqueue_plugin_diagnostic_execute(
    *,
    server_id: int,
    actor_user_id: int,
    scope: Literal["metamod", "counterstrikesharp", "both"],
    expected_plan_hash: str,
) -> dict:
    """Queue a crash-isolation execute on the per-server FIFO."""
    record = await server_operation_hub.create(
        server_id=server_id,
        action="plugin_diagnostic_execute",
        actor_user_id=actor_user_id,
        command=f"plugin-diagnostic execute {scope}",
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_plugin_diagnostic_execute(
            operation_id=operation_id,
            scope=scope,
            expected_plan_hash=expected_plan_hash,
        ),
    )


async def run_plugin_diagnostic_execute(
    *,
    operation_id: str,
    scope: Literal["metamod", "counterstrikesharp", "both"],
    expected_plan_hash: str,
) -> None:
    """Execute one queued plugin crash-isolation plan."""
    from services.plugin_diagnostic_service import execute_diagnostic_plan

    record = await server_operation_hub.get(operation_id)
    if record is None:
        return
    await server_operation_hub.mark_running(operation_id)
    server_id = int(record["server_id"])
    actor_user_id = int(record["actor_user_id"])
    progress = _diagnostic_progress(operation_id)

    try:
        async with async_session_maker() as db:
            user = await db.get(User, actor_user_id)
            if user is None or not user.is_active:
                message = "The operator account is no longer available"
                await server_operation_hub.finish(operation_id, success=False, message=message)
                await _audit_terminal(
                    record,
                    category="plugin",
                    action="plugin.diagnostic.execute",
                    success=False,
                    message=message,
                )
                return
            payload = await execute_diagnostic_plan(
                db,
                user,
                server_id,
                scope,
                expected_plan_hash,
                progress=progress,
            )
        success, message = _diagnostic_success(payload)
        await server_operation_hub.finish(operation_id, success=success, message=message)
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.execute",
            success=success,
            message=message,
            extra={"diagnostic_id": payload.get("id"), "scope": scope},
        )
    except ServerOperationConflict as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.execute",
            success=False,
            message=str(exc),
        )
    except OperationBusyError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.execute",
            success=False,
            message=str(exc),
        )
    except (ValueError, LookupError, RuntimeError) as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.execute",
            success=False,
            message=str(exc),
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await server_operation_hub.finish(operation_id, success=False, message=detail)
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.execute",
            success=False,
            message=detail,
        )
    except Exception:
        logger.exception("Background plugin diagnostic %s failed", operation_id)
        message = "The plugin diagnostic failed unexpectedly"
        await server_operation_hub.finish(operation_id, success=False, message=message)
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.execute",
            success=False,
            message=message,
        )


async def enqueue_plugin_diagnostic_restore(
    *,
    server_id: int,
    actor_user_id: int,
    diagnostic_id: str,
) -> dict:
    """Queue a quarantine restore on the per-server FIFO."""
    record = await server_operation_hub.create(
        server_id=server_id,
        action="plugin_diagnostic_restore",
        actor_user_id=actor_user_id,
        command=f"plugin-diagnostic restore {diagnostic_id}",
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_plugin_diagnostic_restore(
            operation_id=operation_id,
            diagnostic_id=diagnostic_id,
        ),
    )


async def run_plugin_diagnostic_restore(*, operation_id: str, diagnostic_id: str) -> None:
    """Restore one queued plugin-quarantine snapshot."""
    from services.plugin_diagnostic_service import restore_diagnostic_run

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
                message = "The operator account is no longer available"
                await server_operation_hub.finish(operation_id, success=False, message=message)
                await _audit_terminal(
                    record,
                    category="plugin",
                    action="plugin.diagnostic.restore",
                    success=False,
                    message=message,
                )
                return
            payload = await restore_diagnostic_run(db, user, server_id, diagnostic_id)
        success, message = _diagnostic_success(payload)
        await server_operation_hub.finish(operation_id, success=success, message=message)
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.restore",
            success=success,
            message=message,
            extra={"diagnostic_id": diagnostic_id},
        )
    except ServerOperationConflict as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.restore",
            success=False,
            message=str(exc),
        )
    except OperationBusyError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.restore",
            success=False,
            message=str(exc),
        )
    except (LookupError, RuntimeError) as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.restore",
            success=False,
            message=str(exc),
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await server_operation_hub.finish(operation_id, success=False, message=detail)
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.restore",
            success=False,
            message=detail,
        )
    except Exception:
        logger.exception("Background plugin diagnostic restore %s failed", operation_id)
        message = "The plugin diagnostic restore failed unexpectedly"
        await server_operation_hub.finish(operation_id, success=False, message=message)
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.restore",
            success=False,
            message=message,
        )


async def enqueue_plugin_diagnostic_resume(
    *,
    server_id: int,
    actor_user_id: int,
    diagnostic_id: str,
    scope: Literal["metamod", "counterstrikesharp", "both"],
    expected_plan_hash: str,
) -> dict:
    """Queue restore-then-execute for an interrupted diagnostic."""
    record = await server_operation_hub.create(
        server_id=server_id,
        action="plugin_diagnostic_resume",
        actor_user_id=actor_user_id,
        command=f"plugin-diagnostic resume {diagnostic_id}",
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_plugin_diagnostic_resume(
            operation_id=operation_id,
            diagnostic_id=diagnostic_id,
            scope=scope,
            expected_plan_hash=expected_plan_hash,
        ),
    )


async def run_plugin_diagnostic_resume(
    *,
    operation_id: str,
    diagnostic_id: str,
    scope: Literal["metamod", "counterstrikesharp", "both"],
    expected_plan_hash: str,
) -> None:
    """Restore an interrupted diagnostic, then run a fresh isolation plan."""
    from services.plugin_diagnostic_service import (
        execute_diagnostic_plan,
        restore_diagnostic_run,
    )

    record = await server_operation_hub.get(operation_id)
    if record is None:
        return
    await server_operation_hub.mark_running(operation_id)
    server_id = int(record["server_id"])
    actor_user_id = int(record["actor_user_id"])
    progress = _diagnostic_progress(operation_id)

    try:
        async with async_session_maker() as db:
            user = await db.get(User, actor_user_id)
            if user is None or not user.is_active:
                message = "The operator account is no longer available"
                await server_operation_hub.finish(operation_id, success=False, message=message)
                await _audit_terminal(
                    record,
                    category="plugin",
                    action="plugin.diagnostic.resume",
                    success=False,
                    message=message,
                )
                return
            await restore_diagnostic_run(db, user, server_id, diagnostic_id)
            payload = await execute_diagnostic_plan(
                db,
                user,
                server_id,
                scope,
                expected_plan_hash,
                progress=progress,
            )
        success, message = _diagnostic_success(payload)
        await server_operation_hub.finish(operation_id, success=success, message=message)
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.resume",
            success=success,
            message=message,
            extra={"diagnostic_id": diagnostic_id, "scope": scope},
        )
    except ServerOperationConflict as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.resume",
            success=False,
            message=str(exc),
        )
    except OperationBusyError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.resume",
            success=False,
            message=str(exc),
        )
    except (ValueError, LookupError, RuntimeError) as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.resume",
            success=False,
            message=str(exc),
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await server_operation_hub.finish(operation_id, success=False, message=detail)
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.resume",
            success=False,
            message=detail,
        )
    except Exception:
        logger.exception("Background plugin diagnostic resume %s failed", operation_id)
        message = "The plugin diagnostic resume failed unexpectedly"
        await server_operation_hub.finish(operation_id, success=False, message=message)
        await _audit_terminal(
            record,
            category="plugin",
            action="plugin.diagnostic.resume",
            success=False,
            message=message,
        )
