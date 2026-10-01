"""Focused route implementation; the original module retains patchable dependencies."""

from __future__ import annotations

from fastapi import Request

from api.dependencies import ActiveUser, DatabaseSession
from services.compat import LateBoundModule

from .common import *

host = LateBoundModule("api.routes.actions.deployment")
from modules import Server, ServerAction

from .action_context import ActionContext
from .backup_actions import _action_backup_plugins
from .framework_actions import (
    _action_install_counterstrikesharp,
    _action_install_cs2fixes,
    _action_install_metamod,
    _action_install_swiftly,
    _action_update_counterstrikesharp,
    _action_update_cs2fixes,
    _action_update_metamod,
    _action_update_swiftly,
)
from .lifecycle_actions import (
    _action_deploy,
    _action_restart,
    _action_start,
    _action_status,
    _action_stop,
    _action_update,
    _action_validate,
)


async def _record_action_framework(server: Server, current_user: User, action: str) -> None:
    try:
        from services.plugin_auto_update_service import (
            record_framework_installation,
            record_known_github_installation,
        )

        if "cs2fixes" in action:
            await record_known_github_installation(
                server,
                current_user,
                "https://github.com/Source2ZE/CS2Fixes",
                "CS2Fixes",
                "CS2Fixes-*-linux.tar.gz",
            )
            await record_framework_installation(server, current_user, "metamod")
        else:
            framework_key = "metamod" if "metamod" in action else "counterstrikesharp"
            await record_framework_installation(server, current_user, framework_key)
            if framework_key == "counterstrikesharp":
                await record_framework_installation(server, current_user, "metamod")
    except Exception as tracking_error:
        host.logger.warning("Framework installed but tracking metadata failed: %s", tracking_error)


async def _prepare_action_lock(server_id: int, action: str) -> str:
    deployment_lock_key = f"deployment_lock:{server_id}"
    is_deploying = await host.redis_manager.get(deployment_lock_key)

    if is_deploying:
        raise host.HTTPException(
            status_code=host.status.HTTP_409_CONFLICT,
            detail="Server is currently being deployed or has a stuck deployment lock. Please check the console for progress. If the deployment is stuck, you can cancel it from the Actions tab.",
        )

    if action in host.STEAMCMD_ACTIONS:
        await host.prepare_steamcmd_operation(server_id)

    if action == "deploy":
        await host.redis_manager.set(deployment_lock_key, "1", expire=7200)
    return deployment_lock_key


async def execute_server_action(  # noqa: C901
    server_id: int,
    action_data: ServerAction,
    db: DatabaseSession,
    current_user: ActiveUser,
    locked_server: Server | None,
    request: Request | None = None,
    *,
    clear_execstack: bool = False,
    clear_execstack_targets: object = None,
):
    """Execute a validated server action outside the HTTP request boundary."""
    server = (
        locked_server
        if isinstance(locked_server, Server)
        else await host.get_server_and_verify_ownership(db, server_id, current_user)
    )

    action = action_data.action
    deployment_lock_key = await _prepare_action_lock(server_id, action)

    ssh_manager = host.SSHManager()

    log = host.DeploymentLog(server_id=server_id, action=action, status="in_progress")
    db.add(log)
    await db.commit()
    await host.record_audit_event(
        category="server",
        action=f"server.{action}",
        status="requested",
        user=current_user,
        request=request,
        server_id=server_id,
        details={"server_name": server.name},
    )

    if action in {"start", "stop", "restart"}:
        host.apply_user_lifecycle_intent(server, action)
        await db.commit()

    try:
        await host.redis_manager.clear_deployment_progress(server_id)
    except Exception:
        pass

    await host.send_deployment_update(server_id, "status", f"Starting action: {action}")

    async def progress_callback(message: str) -> None:
        await host.send_deployment_update(server_id, "output", message)

    async def clear_crash_protection() -> None:
        note = await host.clear_operator_crash_protection(ssh_manager, server)
        await host.send_deployment_update(
            server_id,
            "output",
            note or "✓ Crash-loop counter cleared for this manual action",
        )

    try:
        if action == "restart":
            manager_ready, preflight_message = await ssh_manager.check_session_manager_available(
                server
            )
            if not manager_ready:
                success = False
                message = (
                    f"Restart aborted before stopping: {preflight_message}. "
                    "The existing game session was left untouched."
                )
                log.status = "failed"
                log.error_message = message
                await host.send_deployment_update(server_id, "error", message)
                await db.commit()
                await db.refresh(server)
                await db.refresh(log)
                await host.redis_manager.set_server_status(server_id, server.status.value)
                await host.send_discord_action_notification(server, action, success, message)
                return host.ActionResponse(
                    success=False,
                    message=message,
                    data={"status": server.status.value},
                )

        context = ActionContext(
            server_id=server_id,
            server=server,
            db=db,
            current_user=current_user,
            ssh_manager=ssh_manager,
            log=log,
            deployment_lock_key=deployment_lock_key,
            progress_callback=progress_callback,
            clear_crash_protection=clear_crash_protection,
            clear_execstack=clear_execstack,
            clear_execstack_targets=clear_execstack_targets,
            action=action,
        )
        handlers = {
            "deploy": _action_deploy,
            "start": _action_start,
            "stop": _action_stop,
            "restart": _action_restart,
            "status": _action_status,
            "update": _action_update,
            "validate": _action_validate,
            "install_metamod": _action_install_metamod,
            "install_counterstrikesharp": _action_install_counterstrikesharp,
            "update_metamod": _action_update_metamod,
            "update_counterstrikesharp": _action_update_counterstrikesharp,
            "install_cs2fixes": _action_install_cs2fixes,
            "update_cs2fixes": _action_update_cs2fixes,
            "install_swiftly": _action_install_swiftly,
            "update_swiftly": _action_update_swiftly,
            "backup_plugins": _action_backup_plugins,
        }
        handler = handlers.get(action)
        if handler is None:
            error_msg = f"Unknown action: {action}"
            log.status = "failed"
            log.error_message = error_msg
            await db.commit()
            await host.send_deployment_update(server_id, "error", error_msg)

            raise host.HTTPException(status_code=host.status.HTTP_400_BAD_REQUEST, detail=error_msg)
        success, message = await handler(context)

        if success:
            await host.maybe_clear_execstack_after_file_action(
                server_id=server_id,
                action=action,
                server=server,
                manager=ssh_manager,
                enabled=clear_execstack,
                targets=clear_execstack_targets,
                report=host.send_deployment_update,
            )

        if success and action in {
            "install_metamod",
            "update_metamod",
            "install_counterstrikesharp",
            "update_counterstrikesharp",
            "install_cs2fixes",
            "update_cs2fixes",
        }:
            await _record_action_framework(server, current_user, action)

        await db.commit()
        await db.refresh(server)
        await db.refresh(log)

        await host.redis_manager.set_server_status(server_id, server.status.value)

        await host.send_discord_action_notification(server, action, success, message)

        return host.ActionResponse(
            success=success, message=message, data={"status": server.status.value}
        )

    except Exception as e:
        log.status = "failed"
        log.error_message = str(e)
        server.status = host.ServerStatus.ERROR
        await db.commit()

        await host.send_deployment_update(server_id, "error", f"Action failed: {str(e)}")
        await host.send_discord_action_notification(server, action, False, str(e))

        raise host.HTTPException(
            status_code=host.status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Action failed: {str(e)}",
        ) from e
