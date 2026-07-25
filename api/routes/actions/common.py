# ruff: noqa: F401
"""
Server actions routes with WebSocket support for real-time deployment status
"""

import asyncio
import json
import logging
import secrets
from contextlib import suppress
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import (
    get_ssh_manager,
    require_server_access,
    resolve_maintenance_lock_service,
    resolve_s3_backup_service,
)
from cs2_manager.core import ErrorResponse
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
from services import SSHManager, redis_manager
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
from services.maintenance_lock import (
    MaintenanceLockService,
    OperationBusyError,
    maintenance_lock_service,
)
from services.s3_backup_service import S3BackupService, s3_backup_service
from services.server_monitor import server_monitor
from services.task_registry import action_task_registry

logger = logging.getLogger(__name__)

DEPLOYMENT_PROGRESS_CLEANUP_DELAY = (
    300  # 5 minutes - allows clients to fetch final messages before cleanup
)

_background_tasks = action_task_registry.tasks

_batch_operation_semaphore = asyncio.Semaphore(8)

_user_batch_semaphores: dict[int, asyncio.Semaphore] = {}

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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    lock_service: MaintenanceLockService = Depends(resolve_maintenance_lock_service),
):
    """Verify ownership and hold the cross-process lock for one mutating action."""
    if not callable(getattr(lock_service, "get", None)):
        # Direct Python callers retain the legacy facade; HTTP requests always
        # receive an application-owned service from the dependency above.
        lock_service = maintenance_lock_service
    server = await get_server_and_verify_ownership(db, server_id, current_user)
    await db.commit()
    async with lock_service.get(
        server_id,
        operation="server_action",
        wait=False,
        ttl=7200,
    ):
        yield server


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
    s3_service: S3BackupService = s3_backup_service,
) -> tuple[bool, str]:
    """Upload the most recent plugin backup produced by SSHManager, if S3 is configured."""
    owner = await get_server_owner(db, server, current_user)
    detached_owner = User.model_validate(owner, from_attributes=True)
    detached_server = Server.model_validate(server, from_attributes=True)
    await db.commit()
    if not s3_service.is_configured(detached_owner):
        return True, ""

    backup_info = getattr(ssh_manager, "last_plugin_backup", None)
    backup_path = backup_info.get("path") if backup_info else None
    if not backup_path:
        message = (
            "Plugin backup completed locally, but the archive path was not captured for S3 upload."
        )
        discord_notification_service.queue_notify(
            detached_server,
            EVENT_S3_BACKUP,
            "s3_backup_upload",
            False,
            message,
            title="S3 backup upload failed",
        )
        return False, message

    upload_success, upload_message, object_key = await s3_service.upload_remote_backup(
        ssh_manager,
        detached_server,
        detached_owner,
        backup_path,
        progress_callback=progress_callback,
    )
    details = {"Backup Archive": backup_path}
    if object_key:
        details["Object Key"] = object_key
    discord_notification_service.queue_notify(
        detached_server,
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


def _spawn_action_task(request: Request, coroutine, *, name: str) -> asyncio.Task:
    """Attach request-created work to the owning application supervisor."""
    supervisor = getattr(request.app.state, "task_supervisor", None)
    if supervisor is not None:
        return supervisor.create(coroutine, name=name)
    return action_task_registry.create(coroutine)


async def shutdown_background_tasks() -> None:
    """Compatibility wrapper for lifecycle-owned task cleanup."""
    await action_task_registry.shutdown()


async def _run_bounded_batch_operation(
    server_id: int,
    user_id: int,
    batch_id: str,
    operation: str,
    callback,
) -> None:
    """Bound global fan-out and serialize destructive work per server."""
    lock_service = getattr(callback, "maintenance_lock_service", maintenance_lock_service)
    user_semaphore = _user_batch_semaphores.setdefault(user_id, asyncio.Semaphore(2))
    try:
        async with user_semaphore:
            async with _batch_operation_semaphore:
                try:
                    async with lock_service.get(
                        server_id,
                        operation=operation,
                        wait=True,
                        wait_timeout=30,
                        ttl=7200,
                    ):
                        await callback()
                except OperationBusyError as exc:
                    await redis_manager.set_batch_action_status(
                        batch_id, server_id, "failed", str(exc)
                    )
    finally:
        async with _pending_batch_counts_lock:
            remaining = max(0, _pending_batch_counts.get(user_id, 1) - 1)
            if remaining:
                _pending_batch_counts[user_id] = remaining
            else:
                _pending_batch_counts.pop(user_id, None)


class _LockBoundCallback:
    """Carry an application lock service into background work without Request."""

    def __init__(self, callback, lock_service: MaintenanceLockService) -> None:
        self._callback = callback
        self.maintenance_lock_service = lock_service

    async def __call__(self):
        return await self._callback()


def _bind_maintenance_lock(callback, lock_service: MaintenanceLockService):
    if not callable(getattr(lock_service, "get", None)):
        lock_service = maintenance_lock_service
    return _LockBoundCallback(callback, lock_service)


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


def _resolve_background_session_factory(session_factory=None):
    """Resolve an injected app database while preserving legacy direct calls."""
    if session_factory is not None:
        return session_factory
    from modules.database import async_session_maker

    return async_session_maker


def _request_session_factory(request: Request):
    """Return the database factory owned by the request's application."""
    container = getattr(request.app.state, "container", None)
    database = getattr(container, "database", None)
    session_factory = getattr(database, "session_factory", None)
    if not callable(session_factory):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Background database sessions are unavailable",
        )
    return session_factory


def _resolve_background_ssh_manager_factory(ssh_manager_factory=None):
    """Preserve the direct-call facade while request paths pass owned resources."""
    return ssh_manager_factory or SSHManager


def _request_ssh_manager_factory(request: Request):
    """Freeze one app's SSH resources into a request-independent factory."""
    prototype = get_ssh_manager(request)
    connection_pool = prototype.connection_pool
    http_resource = prototype.http_resource

    def create_manager() -> SSHManager:
        return SSHManager(
            connection_pool=connection_pool,
            http_resource=http_resource,
        )

    return create_manager


async def execute_single_server_action(
    server_id: int,
    action: str,
    user_id: int,
    is_admin: bool,
    batch_id: str,
    *,
    session_factory=None,
    ssh_manager_factory=None,
):
    """
    Execute an action on a single server in the background.
    This function is designed to run as a background task.

    Args:
        server_id: Server ID
        action: Action to perform (restart, stop, update)
        user_id: User ID for ownership verification
        is_admin: Whether the user is an admin
        batch_id: Batch ID for tracking progress
    """
    import logging

    logger = logging.getLogger(__name__)
    server = None
    session_factory = _resolve_background_session_factory(session_factory)
    ssh_manager_factory = _resolve_background_ssh_manager_factory(ssh_manager_factory)

    try:
        # Update status to in_progress
        await redis_manager.set_batch_action_status(
            batch_id, server_id, "in_progress", "Starting..."
        )

        # Get server and verify ownership - close DB session quickly to avoid pool exhaustion
        async with session_factory() as db:
            if is_admin:
                server_record = await Server.get_by_id(db, server_id)
            else:
                server_record = await Server.get_by_id_and_user(db, server_id, user_id)
            if not server_record:
                await redis_manager.set_batch_action_status(
                    batch_id, server_id, "failed", "Server not found"
                )
                return
            server = Server.model_validate(server_record, from_attributes=True)

        # DB session closed here - perform SSH operations without holding DB connection
        ssh_manager = ssh_manager_factory()
        success = False
        message = ""
        new_status = None

        try:
            if action == "restart":
                (
                    manager_ready,
                    preflight_message,
                ) = await ssh_manager.check_session_manager_available(server)
                if not manager_ready:
                    success = False
                    message = (
                        f"Restart aborted before stopping: {preflight_message}. "
                        "The existing game session was left untouched."
                    )
                else:
                    # Stop then start only after the selected manager is ready.
                    await redis_manager.set_batch_action_status(
                        batch_id, server_id, "in_progress", "Stopping server..."
                    )
                    stop_success, stop_msg = await ssh_manager.stop_server(server)
                    if not stop_success:
                        success = False
                        message = f"Restart stopped because shutdown failed: {stop_msg}"
                        new_status = ServerStatus.ERROR
                    else:
                        # Add small delay before starting a fully stopped server.
                        await asyncio.sleep(0.5)
                        await redis_manager.set_batch_action_status(
                            batch_id, server_id, "in_progress", "Starting server..."
                        )
                        success, message = await ssh_manager.start_server(server)
                        new_status = ServerStatus.RUNNING if success else ServerStatus.ERROR

            elif action == "stop":
                success, message = await ssh_manager.stop_server(server)
                if success:
                    new_status = ServerStatus.STOPPED
                else:
                    new_status = ServerStatus.ERROR

            elif action == "update":
                await redis_manager.set_batch_action_status(
                    batch_id, server_id, "in_progress", "Updating server..."
                )
                success, message = await ssh_manager.update_server(server)
                if not success:
                    new_status = ServerStatus.ERROR

            # Update server status and create deployment log in a separate quick session
            async with session_factory() as db:
                if new_status:
                    server_to_update = await db.get(Server, server_id)
                    if server_to_update:
                        server_to_update.status = new_status
                        await db.commit()

                # Create deployment log
                log = DeploymentLog(
                    server_id=server_id,
                    action=action,
                    status="success" if success else "failed",
                    output=message if success else None,
                    error_message=message if not success else None,
                )
                db.add(log)
                await db.commit()

            # Update final status
            if success:
                await redis_manager.set_batch_action_status(batch_id, server_id, "success", message)
            else:
                await redis_manager.set_batch_action_status(batch_id, server_id, "failed", message)

            await send_discord_action_notification(
                server,
                action,
                success,
                message,
                details={"Batch ID": batch_id},
            )

        except Exception as e:
            logger.error(f"Error executing action {action} on server {server_id}: {e}")
            await redis_manager.set_batch_action_status(batch_id, server_id, "failed", str(e))
            await send_discord_action_notification(
                server,
                action,
                False,
                str(e),
                details={"Batch ID": batch_id},
            )

    except Exception as e:
        logger.error(f"Background task error for server {server_id}: {e}")
        await redis_manager.set_batch_action_status(batch_id, server_id, "failed", str(e))


async def execute_single_server_plugins(
    server_id: int,
    plugins: List[str],
    user_id: int,
    is_admin: bool,
    batch_id: str,
    *,
    session_factory=None,
    ssh_manager_factory=None,
):
    """
    Install plugins on a single server in the background.
    This function is designed to run as a background task.

    Args:
        server_id: Server ID
        plugins: List of plugins to install
        user_id: User ID for ownership verification
        is_admin: Whether the user is an admin
        batch_id: Batch ID for tracking progress
    """
    import logging

    logger = logging.getLogger(__name__)
    server = None
    owner = None
    session_factory = _resolve_background_session_factory(session_factory)
    ssh_manager_factory = _resolve_background_ssh_manager_factory(ssh_manager_factory)

    try:
        # Update status to in_progress
        await redis_manager.set_batch_action_status(
            batch_id, server_id, "in_progress", "Starting plugin installation..."
        )

        # Get server and verify ownership - close DB session quickly to avoid pool exhaustion
        async with session_factory() as db:
            if is_admin:
                server_record = await Server.get_by_id(db, server_id)
            else:
                server_record = await Server.get_by_id_and_user(db, server_id, user_id)

            if not server_record:
                await redis_manager.set_batch_action_status(
                    batch_id, server_id, "failed", "Server not found"
                )
                return
            owner_record = await db.get(User, server_record.user_id)
            server = Server.model_validate(server_record, from_attributes=True)
            owner = (
                User.model_validate(owner_record, from_attributes=True)
                if owner_record is not None
                else None
            )

        # DB session closed here - perform SSH operations without holding DB connection
        ssh_manager = ssh_manager_factory()
        plugin_results = []

        for plugin in plugins:
            try:
                await redis_manager.set_batch_action_status(
                    batch_id, server_id, "in_progress", f"Installing {plugin}..."
                )

                success = False
                message = ""

                if plugin == "metamod":
                    success, message = await ssh_manager.install_metamod(server)
                elif plugin == "counterstrikesharp":
                    success, message = await ssh_manager.install_counterstrikesharp(server)
                elif plugin == "cs2fixes":
                    success, message = await ssh_manager.install_cs2fixes(server)
                else:
                    success = False
                    message = f"Unknown plugin: {plugin}"

                if success and owner:
                    try:
                        from services.plugin_auto_update_service import (
                            record_framework_installation,
                            record_known_github_installation,
                        )

                        if plugin in {"metamod", "counterstrikesharp"}:
                            await record_framework_installation(
                                server,
                                owner,
                                plugin,
                                http_resource=ssh_manager.http_resource,
                                ssh_manager_factory=ssh_manager_factory,
                            )
                            if plugin == "counterstrikesharp":
                                await record_framework_installation(
                                    server,
                                    owner,
                                    "metamod",
                                    http_resource=ssh_manager.http_resource,
                                    ssh_manager_factory=ssh_manager_factory,
                                )
                        elif plugin == "cs2fixes":
                            await record_known_github_installation(
                                server,
                                owner,
                                "https://github.com/Source2ZE/CS2Fixes",
                                "CS2Fixes",
                                "CS2Fixes-*-linux.tar.gz",
                                http_resource=ssh_manager.http_resource,
                            )
                            await record_framework_installation(
                                server,
                                owner,
                                "metamod",
                                http_resource=ssh_manager.http_resource,
                                ssh_manager_factory=ssh_manager_factory,
                            )
                    except Exception as tracking_error:
                        logger.warning(
                            "Plugin installed but tracking metadata failed: %s", tracking_error
                        )

                # Create deployment log in a separate quick session
                async with session_factory() as db:
                    log = DeploymentLog(
                        server_id=server_id,
                        action=f"install_{plugin}",
                        status="success" if success else "failed",
                        output=message if success else None,
                        error_message=message if not success else None,
                    )
                    db.add(log)
                    await db.commit()

                plugin_results.append({"plugin": plugin, "success": success, "message": message})

            except Exception as e:
                logger.error(f"Error installing {plugin} on server {server_id}: {e}")
                plugin_results.append({"plugin": plugin, "success": False, "message": str(e)})

        # Determine overall success
        overall_success = all(r["success"] for r in plugin_results)
        summary = ", ".join(
            [f"{r['plugin']}: {'✓' if r['success'] else '✗'}" for r in plugin_results]
        )

        if overall_success:
            await redis_manager.set_batch_action_status(batch_id, server_id, "success", summary)
        else:
            await redis_manager.set_batch_action_status(batch_id, server_id, "failed", summary)

        await send_discord_action_notification(
            server,
            "batch_install_plugins",
            overall_success,
            summary,
            details={
                "Batch ID": batch_id,
                "Plugins": ", ".join(plugins),
            },
        )

    except Exception as e:
        logger.error(f"Background task error for server {server_id}: {e}")
        await redis_manager.set_batch_action_status(batch_id, server_id, "failed", str(e))
        if server:
            await send_discord_action_notification(
                server,
                "batch_install_plugins",
                False,
                str(e),
                details={
                    "Batch ID": batch_id,
                    "Plugins": ", ".join(plugins),
                },
            )


async def execute_single_server_command(
    server_id: int,
    command: str,
    user_id: int,
    is_admin: bool,
    batch_id: str,
    *,
    session_factory=None,
    ssh_manager_factory=None,
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
    session_factory = _resolve_background_session_factory(session_factory)
    ssh_manager_factory = _resolve_background_ssh_manager_factory(ssh_manager_factory)

    try:
        await redis_manager.set_batch_action_status(
            batch_id, server_id, "in_progress", "Sending command to server..."
        )

        async with session_factory() as db:
            server_record = await db.get(Server, server_id)

            if not server_record:
                await redis_manager.set_batch_action_status(
                    batch_id, server_id, "failed", "Server not found"
                )
                return

            # Verify ownership
            if not is_admin and server_record.user_id != user_id:
                await redis_manager.set_batch_action_status(
                    batch_id, server_id, "failed", "Access denied"
                )
                return
            server = Server.model_validate(server_record, from_attributes=True)

        # The discovery session must be returned to the pool before any slow
        # SSH work starts. Batch commands can fan out to 40 servers, so holding
        # one checkout per target here would exhaust a normally sized DB pool.
        ssh_manager = ssh_manager_factory()
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
