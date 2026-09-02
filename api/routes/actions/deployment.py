"""Actions deployment endpoints."""

# ruff: noqa: F403,F405

from fastapi import Request

from api.dependencies import ActiveUser, DatabaseSession
from api.routes.actions.common import _store_task
from services.audit_log_service import record_audit_event
from services.maintenance_lock import maintenance_lock_service
from services.steamcmd_guard import (
    STEAMCMD_ACTIONS,
    STEAMCMD_FORCE_TERMINATED,
    clear_steamcmd_cancel,
    force_clear_steamcmd_lock,
    prepare_steamcmd_operation,
    request_steamcmd_cancel,
)

from .common import *

router = APIRouter(tags=["actions"])


@router.websocket("/servers/{server_id}/deployment-status")
async def deployment_status_websocket(websocket: WebSocket, server_id: int):
    """
    WebSocket endpoint for real-time deployment status updates

    Sends messages in format:
    {
        "type": "status|output|error|complete",
        "message": "...",
        "timestamp": "2024-01-01T00:00:00"
    }

    On connection, sends all accumulated progress from Redis if available.
    """
    user, server = await authenticate_websocket(websocket, server_id)
    if user is None or server is None:
        return
    await deployment_ws.connect(websocket, server_id)
    try:
        # Send accumulated progress on connection (for recovery after disconnect/restart)
        accumulated_progress = await redis_manager.get_deployment_progress(server_id)
        if accumulated_progress:
            # Send a header message
            await websocket.send_json(
                {
                    "type": "info",
                    "message": f"Recovered {len(accumulated_progress)} progress message(s) from previous session",
                    "timestamp": get_current_time().isoformat(),
                }
            )
            # Send all accumulated progress
            for progress_entry in accumulated_progress:
                await websocket.send_json(progress_entry)

        while True:
            # Keep connection alive and receive any client messages
            await websocket.receive_text()
            # Echo back or handle client messages if needed
            await websocket.send_json(
                {
                    "type": "ack",
                    "message": "Connected to deployment status stream",
                    "timestamp": get_current_time().isoformat(),
                }
            )
    except WebSocketDisconnect:
        deployment_ws.disconnect(websocket, server_id)


@router.get("/servers/{server_id}/deployment-lock")
async def check_deployment_lock(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """
    Check deployment lock status for a server.

    Returns information about whether a deployment lock exists for the specified server,
    which can be used to determine if a deployment operation is in progress or stuck.

    Args:
        server_id: ID of the server to check
        db: Database session (injected)
        current_user: Current authenticated user (injected)

    Returns:
        JSONResponse with:
            - lock_exists (bool): Whether a deployment lock is active
            - server_status (str): Current server status

    Raises:
        HTTPException 404: Server not found or user doesn't own it
    """
    # Verify user owns this server
    server = await get_server_and_verify_ownership(db, server_id, current_user)

    deployment_lock_key = f"deployment_lock:{server_id}"
    lock_exists = await redis_manager.get(deployment_lock_key)

    return JSONResponse(content={"lock_exists": bool(lock_exists), "server_status": server.status})


@router.delete("/servers/{server_id}/deployment-lock")
async def cancel_deployment(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """
    Force-stop a deploy/update/validate: cancel the in-flight operation,
    kill only this server's SteamCMD processes, then release the exclusive lock.
    """
    try:
        server = await get_server_and_verify_ownership(db, server_id, current_user)
        await request_steamcmd_cancel(server_id)

        from services.server_operation_hub import server_operation_hub

        aborted = False
        killed_processes = False
        try:
            aborted = bool(
                await server_operation_hub.abort(server_id, message=STEAMCMD_FORCE_TERMINATED)
            )

            ssh_manager = SSHManager()
            try:
                success, msg = await ssh_manager.connect(server)
                if success:
                    await ssh_manager._kill_steamcmd_processes(server)
                    killed_processes = True
                    logger.info(
                        "Force-stopped SteamCMD processes for server %s (game dir scoped)",
                        server_id,
                    )
                else:
                    logger.warning(
                        f"Could not connect to server {server_id} to kill SteamCMD: {msg}"
                    )
            except Exception as e:
                logger.warning(f"Failed to kill SteamCMD processes for server {server_id}: {e}")
            finally:
                try:
                    await ssh_manager.disconnect()
                except Exception as e:
                    logger.debug(f"Error disconnecting SSH for server {server_id}: {e}")
        finally:
            await force_clear_steamcmd_lock(server_id)
            await clear_steamcmd_cancel(server_id)
            try:
                await maintenance_lock_service.force_release_server_lock(
                    server_id, ignore_local=True
                )
            except Exception:
                logger.debug(
                    "Force-stop could not release maintenance lock for server %s",
                    server_id,
                    exc_info=True,
                )
            try:
                await redis_manager.clear_deployment_progress(server_id)
            except Exception:
                pass

        if server.status == ServerStatus.DEPLOYING:
            server.status = ServerStatus.ERROR
            await db.commit()

        message = "Deployment force-stopped"
        if aborted:
            message += "; in-flight operation cancelled"
        if killed_processes:
            message += "; this server's SteamCMD processes were terminated"
        message += ". You can start a new operation."
        return JSONResponse(content={"success": True, "message": message})
    except HTTPException:
        # Re-raise HTTP exceptions (like 403, 404) to be handled by FastAPI
        raise
    except Exception as e:
        logger.error(f"Error clearing deployment lock for server {server_id}: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"Failed to clear deployment lock: {str(e)}"},
        )


async def execute_server_action(
    server_id: int,
    action_data: ServerAction,
    db: DatabaseSession,
    current_user: ActiveUser,
    locked_server: Server | None,
    request: Request | None = None,
):
    """Execute a validated server action outside the HTTP request boundary."""
    server = (
        locked_server
        if isinstance(locked_server, Server)
        else await get_server_and_verify_ownership(db, server_id, current_user)
    )

    # Check if server is already being deployed (prevent concurrent operations during deployment)
    action = action_data.action
    deployment_lock_key = f"deployment_lock:{server_id}"
    is_deploying = await redis_manager.get(deployment_lock_key)

    if is_deploying:
        # If deployment is in progress, reject all operations
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Server is currently being deployed or has a stuck deployment lock. Please check the console for progress. If the deployment is stuck, you can cancel it from the Actions tab.",
        )

    if action in STEAMCMD_ACTIONS:
        await prepare_steamcmd_operation(server_id)

    # Set deployment lock only for deploy action (with 2 hour expiration in case of crashes)
    if action == "deploy":
        await redis_manager.set(deployment_lock_key, "1", expire=7200)

    ssh_manager = SSHManager()

    # Create deployment log
    log = DeploymentLog(server_id=server_id, action=action, status="in_progress")
    db.add(log)
    await db.commit()
    await record_audit_event(
        category="server",
        action=f"server.{action}",
        status="requested",
        user=current_user,
        request=request,
        server_id=server_id,
        details={"server_name": server.name},
    )

    if action in {"start", "stop", "restart"}:
        apply_user_lifecycle_intent(server, action)
        # The intent must become visible before the remote lifecycle command.
        # A failed Stop intentionally leaves the protection enabled.
        await db.commit()

    # Clear previous websocket records before starting new operation
    # This is a non-critical operation - if it fails, continue with the action
    try:
        await redis_manager.clear_deployment_progress(server_id)
    except Exception:
        # Silently continue if cleanup fails - old messages are better than blocking the operation
        # This catches Redis connection errors, timeouts, and other non-critical failures
        pass

    # Send WebSocket notification
    await send_deployment_update(server_id, "status", f"Starting action: {action}")

    async def progress_callback(message: str) -> None:
        await send_deployment_update(server_id, "output", message)

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
                await send_deployment_update(server_id, "error", message)
                await db.commit()
                await db.refresh(server)
                await db.refresh(log)
                await redis_manager.set_server_status(server_id, server.status.value)
                await send_discord_action_notification(server, action, success, message)
                return ActionResponse(
                    success=False,
                    message=message,
                    data={"status": server.status.value},
                )

        if action == "deploy":
            server.status = ServerStatus.DEPLOYING
            await db.commit()

            try:
                await send_deployment_update(server_id, "status", "Connecting to server via SSH...")
                success, message = await ssh_manager.deploy_cs2_server(server, progress_callback)

                if success:
                    server.status = ServerStatus.STOPPED
                    server.last_deployed = get_current_time()
                    log.status = "success"
                    log.output = message
                    await send_deployment_update(
                        server_id, "complete", "Deployment completed successfully"
                    )
                else:
                    server.status = ServerStatus.ERROR
                    log.status = "failed"
                    log.error_message = message
                    await send_deployment_update(
                        server_id, "error", f"Deployment failed: {message}"
                    )
            finally:
                # ALWAYS remove deployment lock when deployment completes, regardless of success/failure/exception
                await redis_manager.delete(deployment_lock_key)
                # Clear deployment progress after a delay to allow clients to fetch final messages
                # The progress cache will also auto-expire after 2 hours
                _store_task(asyncio.create_task(clear_deployment_progress_after_delay(server_id)))

        elif action == "start":
            await send_deployment_update(server_id, "status", "Starting server...")
            success, message = await ssh_manager.start_server(server, progress_callback)

            if success:
                server.status = ServerStatus.RUNNING
                log.status = "success"
                log.output = message
                # Reset restart history and A2S failure counter after successful manual start
                server_monitor.reset_restart_history(server_id)
                await send_deployment_update(server_id, "complete", "Server started successfully")
            else:
                server.status = ServerStatus.ERROR
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"Start failed: {message}")

        elif action == "stop":
            await send_deployment_update(server_id, "status", "Stopping server...")
            success, message = await ssh_manager.stop_server(server)

            if success:
                server.status = ServerStatus.STOPPED
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "Server stopped successfully")
            else:
                server.status = ServerStatus.ERROR
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"Stop failed: {message}")

        elif action == "restart":
            await send_deployment_update(server_id, "status", "Restarting server...")

            # Auto-cleanup crash history based on offline duration
            should_clear_crash_history = False

            # Check if auto-clear is configured and server was offline long enough
            if server.auto_clear_crash_hours and server.auto_clear_crash_hours > 0:
                if server.last_status_check:
                    offline_duration = get_current_time() - server.last_status_check
                    offline_hours = offline_duration.total_seconds() / 3600

                    if offline_hours >= server.auto_clear_crash_hours:
                        should_clear_crash_history = True
                        await send_deployment_update(
                            server_id,
                            "output",
                            f"⏰ Server offline for {offline_hours:.1f} hours (threshold: {server.auto_clear_crash_hours}h)",
                        )
                else:
                    # No last status check recorded, assume manual restart should clear
                    should_clear_crash_history = True
            else:
                # Always clear on manual restart if auto-clear is not configured
                should_clear_crash_history = True

            # Clean up crash history log if needed
            if should_clear_crash_history:
                try:
                    crash_log_path = f"{server.game_directory}/crash_history.log"
                    cleanup_cmd = f"rm -f {crash_log_path}"
                    await ssh_manager.connect(server)
                    await ssh_manager.execute_command(cleanup_cmd)
                    await ssh_manager.disconnect()
                    await send_deployment_update(
                        server_id, "output", "✓ Crash history cleared for fresh start"
                    )
                except Exception as e:
                    # Non-critical, continue with restart
                    await send_deployment_update(
                        server_id, "output", f"Note: Could not clear crash history: {str(e)}"
                    )
            else:
                await send_deployment_update(
                    server_id,
                    "output",
                    f"ℹ Crash history retained (offline duration below {server.auto_clear_crash_hours}h threshold)",
                )

            # Stop then start with additional verification
            success, message = await ssh_manager.stop_server(server)

            # Always proceed to start, even if stop reports failure
            # The start_server method has its own defensive checks to kill existing sessions
            if not success:
                await send_deployment_update(server_id, "output", f"Stop returned: {message}")
                await send_deployment_update(
                    server_id,
                    "output",
                    "Proceeding with start (defensive checks will ensure cleanup)...",
                )
            else:
                await send_deployment_update(
                    server_id, "output", "Server stopped successfully, starting again..."
                )

            # Add small delay to ensure cleanup
            await asyncio.sleep(0.5)

            success, message = await ssh_manager.start_server(server, progress_callback)
            if success:
                server.status = ServerStatus.RUNNING
                log.status = "success"
                log.output = message
                # Reset restart history and A2S failure counter after successful manual restart
                server_monitor.reset_restart_history(server_id)
                await send_deployment_update(server_id, "complete", "Server restarted successfully")
            else:
                server.status = ServerStatus.ERROR
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"Restart failed: {message}")

        elif action == "status":
            await send_deployment_update(server_id, "status", "Checking server status...")
            success, status_msg = await ssh_manager.get_server_status(server)

            # Update last status check time
            server.last_status_check = get_current_time()

            if success:
                if status_msg == "running":
                    server.status = ServerStatus.RUNNING
                elif status_msg == "stopped":
                    server.status = ServerStatus.STOPPED
                else:
                    server.status = ServerStatus.UNKNOWN

                log.status = "success"
                log.output = status_msg
                message = f"Server is {status_msg}"
                await send_deployment_update(server_id, "complete", message)
            else:
                server.status = ServerStatus.UNKNOWN
                log.status = "failed"
                log.error_message = status_msg
                message = f"Failed to get status: {status_msg}"
                success = False
                await send_deployment_update(server_id, "error", message)

        elif action == "update":
            await send_deployment_update(server_id, "status", "Updating server...")
            success, message = await ssh_manager.update_server(server, progress_callback)

            if success:
                # Keep the same status as before update (or set to STOPPED if it was running)
                server.last_update_time = get_current_time()
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "Server updated successfully")
            else:
                server.status = ServerStatus.ERROR
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"Update failed: {message}")

        elif action == "validate":
            await send_deployment_update(server_id, "status", "Updating and validating server...")
            success, message = await ssh_manager.validate_server(server, progress_callback)

            if success:
                # Keep the same status as before validate
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "Server validated successfully")
            else:
                server.status = ServerStatus.ERROR
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"Validation failed: {message}")

        elif action == "install_metamod":
            await send_deployment_update(server_id, "status", "Installing Metamod:Source...")
            success, message = await ssh_manager.install_metamod(server, progress_callback)

            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(
                    server_id, "complete", "Metamod installed successfully"
                )
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(
                    server_id, "error", f"Metamod installation failed: {message}"
                )

        elif action == "install_counterstrikesharp":
            await send_deployment_update(server_id, "status", "Installing CounterStrikeSharp...")
            success, message = await ssh_manager.install_counterstrikesharp(
                server, progress_callback
            )

            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(
                    server_id, "complete", "CounterStrikeSharp installed successfully"
                )
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(
                    server_id, "error", f"CounterStrikeSharp installation failed: {message}"
                )

        elif action == "update_metamod":
            await send_deployment_update(server_id, "status", "Updating Metamod:Source...")
            success, message = await ssh_manager.update_metamod(server, progress_callback)

            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "Metamod updated successfully")
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(
                    server_id, "error", f"Metamod update failed: {message}"
                )

        elif action == "update_counterstrikesharp":
            await send_deployment_update(server_id, "status", "Updating CounterStrikeSharp...")
            success, message = await ssh_manager.update_counterstrikesharp(
                server, progress_callback
            )

            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(
                    server_id, "complete", "CounterStrikeSharp updated successfully"
                )
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(
                    server_id, "error", f"CounterStrikeSharp update failed: {message}"
                )

        elif action == "install_cs2fixes":
            await send_deployment_update(server_id, "status", "Installing CS2Fixes...")
            success, message = await ssh_manager.install_cs2fixes(server, progress_callback)

            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(
                    server_id, "complete", "CS2Fixes installed successfully"
                )
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(
                    server_id, "error", f"CS2Fixes installation failed: {message}"
                )

        elif action == "update_cs2fixes":
            await send_deployment_update(server_id, "status", "Updating CS2Fixes...")
            success, message = await ssh_manager.update_cs2fixes(server, progress_callback)

            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "CS2Fixes updated successfully")
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(
                    server_id, "error", f"CS2Fixes update failed: {message}"
                )

        elif action == "install_swiftly":
            await send_deployment_update(server_id, "status", "Installing SwiftlyS2...")
            success, message = await ssh_manager.install_swiftly(server, progress_callback)

            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(
                    server_id, "complete", "SwiftlyS2 installed successfully"
                )
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(
                    server_id, "error", f"SwiftlyS2 installation failed: {message}"
                )

        elif action == "update_swiftly":
            await send_deployment_update(server_id, "status", "Updating SwiftlyS2...")
            success, message = await ssh_manager.update_swiftly(server, progress_callback)

            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(
                    server_id, "complete", "SwiftlyS2 updated successfully"
                )
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(
                    server_id, "error", f"SwiftlyS2 update failed: {message}"
                )

        elif action == "backup_plugins":
            await send_deployment_update(server_id, "status", "Backing up plugins...")
            success, message = await ssh_manager.backup_plugins(server, progress_callback)

            if success:
                s3_success, s3_message = await upload_latest_plugin_backup_to_s3(
                    db,
                    server,
                    current_user,
                    ssh_manager,
                    progress_callback=progress_callback,
                )
                if s3_success:
                    if s3_message:
                        message = f"{message}\n{s3_message}"
                    log.status = "success"
                    log.output = message
                    await send_deployment_update(
                        server_id, "complete", "Plugins backed up successfully"
                    )
                else:
                    success = False
                    message = f"{message}\n{s3_message}"
                    log.status = "failed"
                    log.error_message = message
                    await send_deployment_update(
                        server_id, "error", f"Plugin backup S3 upload failed: {s3_message}"
                    )
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"Plugin backup failed: {message}")

        else:
            # Handle unknown action
            error_msg = f"Unknown action: {action}"
            log.status = "failed"
            log.error_message = error_msg
            await db.commit()
            await send_deployment_update(server_id, "error", error_msg)

            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)

        if success and action in {
            "install_metamod",
            "update_metamod",
            "install_counterstrikesharp",
            "update_counterstrikesharp",
            "install_cs2fixes",
            "update_cs2fixes",
        }:
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
                logger.warning(
                    "Framework installed but tracking metadata failed: %s", tracking_error
                )

        await db.commit()
        await db.refresh(server)
        await db.refresh(log)

        # Update cache
        await redis_manager.set_server_status(server_id, server.status.value)

        await send_discord_action_notification(server, action, success, message)

        return ActionResponse(
            success=success, message=message, data={"status": server.status.value}
        )

    except Exception as e:
        log.status = "failed"
        log.error_message = str(e)
        server.status = ServerStatus.ERROR
        await db.commit()

        await send_deployment_update(server_id, "error", f"Action failed: {str(e)}")
        await send_discord_action_notification(server, action, False, str(e))

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Action failed: {str(e)}"
        ) from e


@router.post("/servers/{server_id}/actions", response_model=ActionResponse)
async def server_action(
    server_id: int,
    action_data: ServerAction,
    db: DatabaseSession,
    current_user: ActiveUser,
    locked_server: ServerActionLock,
    request: Request,
) -> ActionResponse:
    """Execute action on server (deploy, start, stop, restart, status)."""
    return await execute_server_action(
        server_id,
        action_data,
        db,
        current_user,
        locked_server,
        request,
    )


@router.get("/servers/{server_id}/deployment-progress")
async def get_deployment_progress(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """
    Get accumulated deployment progress for a server

    This endpoint allows clients to retrieve deployment progress after reconnecting
    or if the WebSocket connection was lost. Useful for recovering progress after
    program restart or SSH disconnect.
    """
    await get_server_and_verify_ownership(db, server_id, current_user)

    # Get accumulated progress from Redis
    progress = await redis_manager.get_deployment_progress(server_id)

    return {"server_id": server_id, "progress_messages": progress, "total_messages": len(progress)}


@router.get("/servers/{server_id}/logs", response_model=List[DeploymentLogResponse])
async def get_server_logs(
    server_id: int,
    skip: int = 0,
    limit: int = 50,
    *,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """Get deployment logs for a server"""
    await get_server_and_verify_ownership(db, server_id, current_user)

    logs = await DeploymentLog.get_logs_by_server(db, server_id, skip, limit)

    return logs
