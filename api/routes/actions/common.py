# ruff: noqa: F401
"""
Server actions routes with WebSocket support for real-time deployment status
"""

import asyncio
import json
import logging
import secrets
from contextlib import suppress
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import ActiveUser, DatabaseSession, require_server_access
from modules import (
    ActionResponse,
    BatchActionRequest,
    BatchActionResponse,
    BatchInstallPluginsRequest,
    BatchSendCommandRequest,
    DeploymentLog,
    DeploymentLogResponse,
    Server,
    ServerAction,
    ServerStatus,
    User,
    authenticate_websocket,
    get_current_active_user,
    get_current_time,
    get_db,
)
from modules.database import async_session_maker
from services import SSHManager, redis_manager
from services.concurrency_limiter import KeyedConcurrencyLimiter
from services.deployment_progress import (
    DeploymentWebSocket,
    deployment_ws,
    send_deployment_update,
)
from services.discord_notification_service import (
    EVENT_MANUAL_UPDATE,
    EVENT_PLUGIN_UPDATE,
    EVENT_S3_BACKUP,
    discord_notification_service,
)
from services.game_session import (
    attach_command,
    find_running_session_manager,
    send_keys_command,
    session_name,
)
from services.maintenance_lock import OperationBusyError, maintenance_lock_service
from services.s3_backup_service import s3_backup_service
from services.server_lifecycle_policy import apply_user_lifecycle_intent
from services.server_monitor import server_monitor
from services.task_registry import action_task_registry

logger = logging.getLogger(__name__)

DEPLOYMENT_PROGRESS_CLEANUP_DELAY = (
    300  # 5 minutes - allows clients to fetch final messages before cleanup
)

_background_tasks = action_task_registry.tasks

_batch_operation_limiter = KeyedConcurrencyLimiter[int](global_limit=8, per_key_limit=2)

_pending_batch_counts: dict[int, int] = {}

_pending_batch_counts_lock = asyncio.Lock()

MAX_PENDING_BATCH_OPERATIONS_PER_USER = 40

DISCORD_ACTION_EVENT_TYPES = {
    "update": EVENT_MANUAL_UPDATE,
    "validate": EVENT_MANUAL_UPDATE,
    "install_metamod": EVENT_PLUGIN_UPDATE,
    "install_counterstrikesharp": EVENT_PLUGIN_UPDATE,
    "install_cs2fixes": EVENT_PLUGIN_UPDATE,
    "install_swiftly": EVENT_PLUGIN_UPDATE,
    "update_metamod": EVENT_PLUGIN_UPDATE,
    "update_counterstrikesharp": EVENT_PLUGIN_UPDATE,
    "update_cs2fixes": EVENT_PLUGIN_UPDATE,
    "update_swiftly": EVENT_PLUGIN_UPDATE,
    "backup_plugins": EVENT_PLUGIN_UPDATE,
    "batch_install_plugins": EVENT_PLUGIN_UPDATE,
}


async def get_server_and_verify_ownership(db: AsyncSession, server_id: int, user: User) -> Server:
    """
    Get server by ID and verify user ownership.
    Admins can access any server, regular users can only access their own.
    Raises HTTPException if server not found or user doesn't have access.
    """
    return await require_server_access(db, server_id, user)


async def acquire_server_action_lock(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """Verify ownership and hold the cross-process lock for one mutating action."""
    server = await get_server_and_verify_ownership(db, server_id, current_user)
    await db.commit()
    async with maintenance_lock_service.get(
        server_id,
        operation="server_action",
        wait=False,
        ttl=7200,
    ):
        yield server


ServerActionLock = Annotated[Server, Depends(acquire_server_action_lock)]


async def get_server_owner(db: AsyncSession, server: Server, current_user: User) -> User:
    """Get the account that owns the server, even when an admin is operating it."""
    if current_user.id == server.user_id:
        return current_user

    owner = await db.get(User, server.user_id)
    if not owner:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server owner not found")
    await db.commit()
    return owner


async def upload_latest_plugin_backup_to_s3(
    db: AsyncSession,
    server: Server,
    current_user: User,
    ssh_manager: SSHManager,
    progress_callback=None,
) -> tuple[bool, str]:
    """Upload the most recent plugin backup produced by SSHManager, if S3 is configured."""
    owner = await get_server_owner(db, server, current_user)
    if not s3_backup_service.is_configured(owner):
        return True, ""

    backup_info = getattr(ssh_manager, "last_plugin_backup", None)
    backup_path = backup_info.get("path") if backup_info else None
    if not backup_path:
        message = (
            "Plugin backup completed locally, but the archive path was not captured for S3 upload."
        )
        discord_notification_service.queue_notify(
            server,
            EVENT_S3_BACKUP,
            "s3_backup_upload",
            False,
            message,
            title="S3 backup upload failed",
        )
        return False, message

    upload_success, upload_message, object_key = await s3_backup_service.upload_remote_backup(
        ssh_manager,
        server,
        owner,
        backup_path,
        progress_callback=progress_callback,
    )
    details = {"Backup Archive": backup_path}
    if object_key:
        details["Object Key"] = object_key
    discord_notification_service.queue_notify(
        server,
        EVENT_S3_BACKUP,
        "s3_backup_upload",
        upload_success,
        upload_message,
        title=f"S3 backup upload {'completed' if upload_success else 'failed'}",
        details=details,
    )
    return upload_success, upload_message


def _store_task(task: asyncio.Task) -> None:
    """Store a task to prevent garbage collection and remove when done."""
    action_task_registry.add(task)


async def shutdown_background_tasks() -> None:
    """Compatibility wrapper for lifecycle-owned task cleanup."""
    await action_task_registry.shutdown()


async def _run_bounded_batch_operation(
    server_id: int,
    user_id: int,
    batch_id: str,
    operation: str,
    callback,
    *,
    acquire_lock: bool = True,
):
    """Bound global fan-out. Lifecycle/plugin jobs join the per-server hub FIFO.

    ``acquire_lock`` stays on for short game-console commands that do not go
    through the hub. Hub-backed jobs must not hold the lock or the worker
    cannot start.
    """
    try:
        async with _batch_operation_limiter.slot(user_id):
            try:
                if acquire_lock:
                    async with maintenance_lock_service.get(
                        server_id,
                        operation=operation,
                        wait=True,
                        wait_timeout=30,
                        ttl=7200,
                    ):
                        await callback()
                else:
                    await callback()
            except OperationBusyError as exc:
                await redis_manager.set_batch_action_status(batch_id, server_id, "failed", str(exc))
    finally:
        async with _pending_batch_counts_lock:
            remaining = max(0, _pending_batch_counts.get(user_id, 1) - 1)
            if remaining:
                _pending_batch_counts[user_id] = remaining
            else:
                _pending_batch_counts.pop(user_id, None)


async def _reserve_batch_capacity(user_id: int, operation_count: int) -> None:
    async with _pending_batch_counts_lock:
        pending = _pending_batch_counts.get(user_id, 0)
        if pending + operation_count > MAX_PENDING_BATCH_OPERATIONS_PER_USER:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"A user may queue at most {MAX_PENDING_BATCH_OPERATIONS_PER_USER} server operations",
            )
        _pending_batch_counts[user_id] = pending + operation_count


async def send_discord_action_notification(
    server: Optional[Server],
    action: str,
    success: bool,
    message: str,
    *,
    details: dict | None = None,
) -> None:
    """Send Discord notifications for update-related actions."""
    if server is None:
        return

    event_type = DISCORD_ACTION_EVENT_TYPES.get(action)
    if not event_type:
        return

    discord_notification_service.queue_notify(
        server,
        event_type,
        action,
        success,
        message,
        title=f"{action.replace('_', ' ').title()} {'completed' if success else 'failed'}",
        details=details,
    )


async def clear_deployment_progress_after_delay(
    server_id: int, delay: int = DEPLOYMENT_PROGRESS_CLEANUP_DELAY
):
    """
    Clear deployment progress after a delay

    This delay allows clients to retrieve the final deployment messages after the deployment
    completes. Without the delay, clients reconnecting shortly after deployment completion
    would not be able to see the final status. The progress also auto-expires after 2 hours
    as a fallback.

    Args:
        server_id: Server ID
        delay: Delay in seconds before clearing (default: 5 minutes)
    """
    await asyncio.sleep(delay)
    await redis_manager.clear_deployment_progress(server_id)


async def execute_single_server_action(
    server_id: int, action: str, user_id: int, is_admin: bool, batch_id: str
):
    """Enqueue one lifecycle action onto the per-server FIFO and wait for it."""
    import logging

    logger = logging.getLogger(__name__)
    server = None

    try:
        await redis_manager.set_batch_action_status(
            batch_id, server_id, "in_progress", "Queued on the server task list..."
        )

        async with async_session_maker() as db:
            if is_admin:
                server = await Server.get_by_id(db, server_id)
            else:
                server = await Server.get_by_id_and_user(db, server_id, user_id)
            if not server:
                await redis_manager.set_batch_action_status(
                    batch_id, server_id, "failed", "Server not found"
                )
                return

            if action in {"start", "stop", "restart"}:
                apply_user_lifecycle_intent(server, action)
                await db.commit()

        from api.routes.v1.operation_runner import enqueue_server_operation
        from services.server_operation_hub import ServerOperationConflict, server_operation_hub

        try:
            record = await enqueue_server_operation(
                server_id=server_id,
                action=action,
                actor_user_id=user_id,
            )
        except ServerOperationConflict as exc:
            await redis_manager.set_batch_action_status(batch_id, server_id, "failed", str(exc))
            return

        final = await server_operation_hub.wait_until_terminal(str(record["operation_id"]))
        success = bool(final.get("success"))
        message = str(final.get("message") or "")
        await redis_manager.set_batch_action_status(
            batch_id,
            server_id,
            "success" if success else "failed",
            message,
        )
    except Exception as e:
        logger.error(f"Background task error for server {server_id}: {e}")
        await redis_manager.set_batch_action_status(batch_id, server_id, "failed", str(e))


async def execute_single_server_plugins(
    server_id: int, plugins: List[str], user_id: int, is_admin: bool, batch_id: str
):
    """Enqueue framework installs onto the per-server FIFO, one plugin at a time."""
    import logging

    logger = logging.getLogger(__name__)
    plugin_actions = {
        "metamod": "install_metamod",
        "counterstrikesharp": "install_counterstrikesharp",
        "cs2fixes": "install_cs2fixes",
    }

    try:
        await redis_manager.set_batch_action_status(
            batch_id, server_id, "in_progress", "Queued plugin installation..."
        )

        async with async_session_maker() as db:
            if is_admin:
                server = await Server.get_by_id(db, server_id)
            else:
                server = await Server.get_by_id_and_user(db, server_id, user_id)
            if not server:
                await redis_manager.set_batch_action_status(
                    batch_id, server_id, "failed", "Server not found"
                )
                return

        from api.routes.v1.operation_runner import enqueue_server_operation
        from services.server_operation_hub import ServerOperationConflict, server_operation_hub

        plugin_results = []
        for plugin in plugins:
            action = plugin_actions.get(plugin)
            if action is None:
                plugin_results.append(
                    {"plugin": plugin, "success": False, "message": f"Unknown plugin: {plugin}"}
                )
                continue
            await redis_manager.set_batch_action_status(
                batch_id, server_id, "in_progress", f"Queued {plugin}..."
            )
            try:
                record = await enqueue_server_operation(
                    server_id=server_id,
                    action=action,
                    actor_user_id=user_id,
                )
            except ServerOperationConflict as exc:
                plugin_results.append({"plugin": plugin, "success": False, "message": str(exc)})
                break
            final = await server_operation_hub.wait_until_terminal(str(record["operation_id"]))
            success = bool(final.get("success"))
            message = str(final.get("message") or "")
            plugin_results.append({"plugin": plugin, "success": success, "message": message})
            if not success:
                break

        overall_success = bool(plugin_results) and all(item["success"] for item in plugin_results)
        summary = ", ".join(
            f"{item['plugin']}: {'ok' if item['success'] else 'failed'}" for item in plugin_results
        )
        await redis_manager.set_batch_action_status(
            batch_id,
            server_id,
            "success" if overall_success else "failed",
            summary,
        )
    except Exception as e:
        logger.error(f"Background task error for server {server_id}: {e}")
        await redis_manager.set_batch_action_status(batch_id, server_id, "failed", str(e))


async def execute_single_server_command(
    server_id: int, command: str, user_id: int, is_admin: bool, batch_id: str
):
    """
    Send a command to a single game server in the background.
    This function is designed to run as a background task.

    Args:
        server_id: Server ID to send command to
        command: Command to send to the game server
        user_id: User ID who initiated the command
        is_admin: Whether the user is an admin
        batch_id: Batch ID for tracking progress
    """
    from modules.database import async_session_maker

    try:
        await redis_manager.set_batch_action_status(
            batch_id, server_id, "in_progress", "Sending command to server..."
        )

        async with async_session_maker() as db:
            server = await db.get(Server, server_id)

            if not server:
                await redis_manager.set_batch_action_status(
                    batch_id, server_id, "failed", "Server not found"
                )
                return

            # Verify ownership
            if not is_admin and server.user_id != user_id:
                await redis_manager.set_batch_action_status(
                    batch_id, server_id, "failed", "Access denied"
                )
                return

            # Connect to server via SSH
            ssh_manager = SSHManager()
            success, msg = await ssh_manager.connect(server)

            if not success:
                await redis_manager.set_batch_action_status(
                    batch_id, server_id, "failed", f"SSH connection failed: {msg}"
                )
                return

            try:
                # Detect the configured manager first and the legacy manager
                # second, so switching settings cannot orphan a running session.
                name = session_name(server_id)
                active_manager = await find_running_session_manager(
                    ssh_manager.execute_command,
                    server.session_manager,
                    name,
                )

                if not active_manager:
                    await redis_manager.set_batch_action_status(
                        batch_id,
                        server_id,
                        "failed",
                        "Game server is not running. Please start the server first.",
                    )
                    return

                await redis_manager.set_batch_action_status(
                    batch_id, server_id, "in_progress", f"Executing command: {command}"
                )

                input_cmd = send_keys_command(active_manager, name, command)
                success, stdout, stderr = await ssh_manager.execute_command(input_cmd, timeout=10)

                if success:
                    await redis_manager.set_batch_action_status(
                        batch_id, server_id, "success", f"Command sent successfully: {command}"
                    )
                else:
                    await redis_manager.set_batch_action_status(
                        batch_id,
                        server_id,
                        "failed",
                        f"Failed to send command: {stderr or 'Unknown error'}",
                    )

            finally:
                await ssh_manager.disconnect()

    except Exception as e:
        logger.error(f"Error sending command to server {server_id}: {e}")
        await redis_manager.set_batch_action_status(batch_id, server_id, "failed", str(e))


# Export private helpers too: endpoint modules are mechanical domain slices.
__all__ = [name for name in globals() if not name.startswith("__")]
