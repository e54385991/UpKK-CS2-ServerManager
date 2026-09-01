"""Maintenance operation workers."""

from __future__ import annotations

from fastapi import HTTPException

from api.dependencies import require_server_access
from modules import User
from modules.database import async_session_maker
from services.maintenance_lock import OperationBusyError
from services.server_operation_hub import (
    ServerOperationConflict,
    server_operation_hub,
)

from .shared import _audit_terminal, _dispatch, _progress_emitter, logger

_PLUGIN_UPDATE_DISABLED = "Plugin auto-update is disabled"


async def enqueue_plugin_auto_update(
    *,
    server_id: int,
    actor_user_id: int,
    plugin_id: int | None = None,
    force: bool = True,
) -> dict:
    """Queue a managed-plugin update check on the per-server FIFO."""
    if plugin_id is not None:
        action = "plugin_auto_update_test"
        command = f"plugin-auto-update test {plugin_id}"
    elif force:
        action = "plugin_auto_update"
        command = "plugin-auto-update check --force"
    else:
        action = "plugin_auto_update"
        command = "plugin-auto-update check"
    record = await server_operation_hub.create(
        server_id=server_id,
        action=action,
        actor_user_id=actor_user_id,
        command=command,
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_plugin_auto_update(
            operation_id=operation_id,
            plugin_id=plugin_id,
            force=force,
        ),
    )


async def run_plugin_auto_update(
    *,
    operation_id: str,
    plugin_id: int | None,
    force: bool,
) -> None:
    """Run one queued plugin auto-update or single-plugin test."""
    record = await server_operation_hub.get(operation_id)
    if record is None:
        return

    await server_operation_hub.mark_running(operation_id)
    server_id = int(record["server_id"])
    actor_user_id = int(record["actor_user_id"])
    progress = _progress_emitter(operation_id)
    audit_action = "plugin.auto_update.test" if plugin_id is not None else "plugin.auto_update.run"
    from services.plugin_auto_update_service import plugin_auto_update_service

    try:
        async with async_session_maker() as db:
            user = await db.get(User, actor_user_id)
            if user is None or not user.is_active:
                message = "The operator account is no longer available"
                await server_operation_hub.finish(operation_id, success=False, message=message)
                await _audit_terminal(
                    record,
                    category="plugin",
                    action=audit_action,
                    success=False,
                    message=message,
                )
                return
            await require_server_access(db, server_id, user)

        plugin_auto_update_service.set_progress_sink(server_id, progress)
        try:
            result = await plugin_auto_update_service.check_server(
                server_id, force=force, plugin_id=plugin_id
            )
        finally:
            plugin_auto_update_service.clear_progress_sink(server_id)

        message = str(result.get("message") or "Plugin update finished")
        success = bool(result.get("success")) or message == _PLUGIN_UPDATE_DISABLED
        await server_operation_hub.finish(operation_id, success=success, message=message)
        await _audit_terminal(
            record,
            category="plugin",
            action=audit_action,
            success=success,
            message=message,
            extra={"plugin_id": plugin_id} if plugin_id is not None else None,
        )
    except ServerOperationConflict as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
        await _audit_terminal(
            record, category="plugin", action=audit_action, success=False, message=str(exc)
        )
    except OperationBusyError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
        await _audit_terminal(
            record, category="plugin", action=audit_action, success=False, message=str(exc)
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await server_operation_hub.finish(operation_id, success=False, message=detail)
        await _audit_terminal(
            record, category="plugin", action=audit_action, success=False, message=detail
        )
    except Exception:
        logger.exception("Background plugin auto-update %s failed", operation_id)
        message = "The plugin update failed unexpectedly"
        await server_operation_hub.finish(operation_id, success=False, message=message)
        await _audit_terminal(
            record, category="plugin", action=audit_action, success=False, message=message
        )
