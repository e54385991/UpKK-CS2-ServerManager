"""
Server actions routes with WebSocket support for real-time deployment status
"""
from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from typing import List, Set
import asyncio
import json
import uuid
import secrets

from modules import (
    Server, DeploymentLog, ServerStatus,
    ServerAction, ActionResponse, DeploymentLogResponse,
    BatchActionRequest, BatchActionResponse, BatchInstallPluginsRequest,
    get_db, User, get_current_active_user, get_current_time
)
from modules.database import async_session_maker
from services import SSHManager, redis_manager

router = APIRouter(tags=["actions"])

# Constants
DEPLOYMENT_PROGRESS_CLEANUP_DELAY = 300  # 5 minutes - allows clients to fetch final messages before cleanup

# Store for background tasks to prevent garbage collection
# Tasks are automatically removed when completed via callback
_background_tasks: Set[asyncio.Task] = set()


async def get_server_and_verify_ownership(
    db: AsyncSession, server_id: int, user_id: int
) -> Server:
    """
    Get server by ID and verify user ownership.
    Raises HTTPException if server not found or user doesn't own it.
    """
    server = await Server.get_by_id_and_user(db, server_id, user_id)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found"
        )
    
    return server


def _store_task(task: asyncio.Task) -> None:
    """Store a task to prevent garbage collection and remove when done."""
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


class DeploymentWebSocket:
    """WebSocket manager for deployment status updates"""
    
    def __init__(self):
        self.active_connections: dict[int, list[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, server_id: int):
        """Connect a WebSocket client"""
        await websocket.accept()
        if server_id not in self.active_connections:
            self.active_connections[server_id] = []
        self.active_connections[server_id].append(websocket)
    
    def disconnect(self, websocket: WebSocket, server_id: int):
        """Disconnect a WebSocket client"""
        if server_id in self.active_connections:
            if websocket in self.active_connections[server_id]:
                self.active_connections[server_id].remove(websocket)
            if not self.active_connections[server_id]:
                del self.active_connections[server_id]
    
    async def send_message(self, server_id: int, message: dict):
        """Send message to all connected clients for a server"""
        if server_id in self.active_connections:
            disconnected = []
            for connection in self.active_connections[server_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    disconnected.append(connection)
            
            # Remove disconnected clients
            for connection in disconnected:
                self.disconnect(connection, server_id)


deployment_ws = DeploymentWebSocket()


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
    await deployment_ws.connect(websocket, server_id)
    try:
        # Send accumulated progress on connection (for recovery after disconnect/restart)
        accumulated_progress = await redis_manager.get_deployment_progress(server_id)
        if accumulated_progress:
            # Send a header message
            await websocket.send_json({
                "type": "info",
                "message": f"Recovered {len(accumulated_progress)} progress message(s) from previous session",
                "timestamp": get_current_time().isoformat()
            })
            # Send all accumulated progress
            for progress_entry in accumulated_progress:
                await websocket.send_json(progress_entry)
        
        while True:
            # Keep connection alive and receive any client messages
            data = await websocket.receive_text()
            # Echo back or handle client messages if needed
            await websocket.send_json({
                "type": "ack",
                "message": "Connected to deployment status stream",
                "timestamp": get_current_time().isoformat()
            })
    except WebSocketDisconnect:
        deployment_ws.disconnect(websocket, server_id)


async def send_deployment_update(server_id: int, msg_type: str, message: str):
    """Helper to send deployment updates via WebSocket and persist to Redis"""
    timestamp = get_current_time().isoformat()
    
    # Send via WebSocket to active connections
    await deployment_ws.send_message(server_id, {
        "type": msg_type,
        "message": message,
        "timestamp": timestamp
    })
    
    # Persist to Redis for recovery after disconnect/restart
    await redis_manager.append_deployment_progress(server_id, msg_type, message, timestamp)


async def clear_deployment_progress_after_delay(server_id: int, delay: int = DEPLOYMENT_PROGRESS_CLEANUP_DELAY):
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


@router.get("/servers/{server_id}/deployment-lock")
async def check_deployment_lock(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
    server = await get_server_and_verify_ownership(db, server_id, current_user.id)
    
    deployment_lock_key = f"deployment_lock:{server_id}"
    lock_exists = await redis_manager.get(deployment_lock_key)
    
    return JSONResponse(
        content={
            "lock_exists": bool(lock_exists),
            "server_status": server.status
        }
    )


@router.delete("/servers/{server_id}/deployment-lock")
async def cancel_deployment(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Cancel an in-progress or stuck deployment by clearing the deployment lock.
    
    This endpoint allows users to clear a deployment lock that may be stuck
    due to interruptions, crashes, or other issues. Use with caution as clearing
    the lock while a deployment is actually running may cause issues.
    """
    # Verify user owns this server
    server = await get_server_and_verify_ownership(db, server_id, current_user.id)
    
    deployment_lock_key = f"deployment_lock:{server_id}"
    lock_exists = await redis_manager.get(deployment_lock_key)
    
    if not lock_exists:
        return JSONResponse(
            content={
                "success": True,
                "message": "No deployment lock found for this server"
            }
        )
    
    # Clear the deployment lock
    await redis_manager.delete(deployment_lock_key)
    
    # Also clear deployment progress
    await redis_manager.clear_deployment_progress(server_id)
    
    # Update server status if it's stuck in DEPLOYING
    if server.status == ServerStatus.DEPLOYING:
        server.status = ServerStatus.ERROR
        await db.commit()
    
    return JSONResponse(
        content={
            "success": True,
            "message": "Deployment lock cleared successfully. You can now start a new operation."
        }
    )


@router.post("/servers/{server_id}/actions", response_model=ActionResponse)
async def server_action(
    server_id: int,
    action_data: ServerAction,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Execute action on server (deploy, start, stop, restart, status)"""
    server = await get_server_and_verify_ownership(db, server_id, current_user.id)
    
    # Check if server is already being deployed (prevent concurrent operations during deployment)
    action = action_data.action
    deployment_lock_key = f"deployment_lock:{server_id}"
    is_deploying = await redis_manager.get(deployment_lock_key)
    
    if is_deploying:
        # If deployment is in progress, reject all operations
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Server is currently being deployed or has a stuck deployment lock. Please check the console for progress. If the deployment is stuck, you can cancel it from the Actions tab."
        )
    
    # Set deployment lock only for deploy action (with 2 hour expiration in case of crashes)
    if action == "deploy":
        await redis_manager.set(deployment_lock_key, "1", expire=7200)
    
    ssh_manager = SSHManager()
    
    # Create deployment log
    log = DeploymentLog(
        server_id=server_id,
        action=action,
        status="in_progress"
    )
    db.add(log)
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
    
    try:
        if action == "deploy":
            server.status = ServerStatus.DEPLOYING
            await db.commit()
            
            try:
                await send_deployment_update(server_id, "status", "Connecting to server via SSH...")
                success, message = await ssh_manager.deploy_cs2_server(server, 
                                                                       lambda msg: asyncio.create_task(
                                                                           send_deployment_update(server_id, "output", msg)
                                                                       ))
                
                if success:
                    server.status = ServerStatus.STOPPED
                    server.last_deployed = get_current_time()
                    log.status = "success"
                    log.output = message
                    await send_deployment_update(server_id, "complete", "Deployment completed successfully")
                else:
                    server.status = ServerStatus.ERROR
                    log.status = "failed"
                    log.error_message = message
                    await send_deployment_update(server_id, "error", f"Deployment failed: {message}")
            finally:
                # ALWAYS remove deployment lock when deployment completes, regardless of success/failure/exception
                await redis_manager.delete(deployment_lock_key)
                # Clear deployment progress after a delay to allow clients to fetch final messages
                # The progress cache will also auto-expire after 2 hours
                asyncio.create_task(clear_deployment_progress_after_delay(server_id))
            
        elif action == "start":
            await send_deployment_update(server_id, "status", "Starting server...")
            success, message = await ssh_manager.start_server(server,
                                                             lambda msg: asyncio.create_task(
                                                                 send_deployment_update(server_id, "output", msg)
                                                             ))
            
            if success:
                server.status = ServerStatus.RUNNING
                log.status = "success"
                log.output = message
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
                            f"⏰ Server offline for {offline_hours:.1f} hours (threshold: {server.auto_clear_crash_hours}h)"
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
                    await send_deployment_update(server_id, "output", "✓ Crash history cleared for fresh start")
                except Exception as e:
                    # Non-critical, continue with restart
                    await send_deployment_update(server_id, "output", f"Note: Could not clear crash history: {str(e)}")
            else:
                await send_deployment_update(
                    server_id, 
                    "output", 
                    f"ℹ Crash history retained (offline duration below {server.auto_clear_crash_hours}h threshold)"
                )
            
            # Stop then start with additional verification
            success, message = await ssh_manager.stop_server(server)
            
            # Always proceed to start, even if stop reports failure
            # The start_server method has its own defensive checks to kill existing sessions
            if not success:
                await send_deployment_update(server_id, "output", f"Stop returned: {message}")
                await send_deployment_update(server_id, "output", "Proceeding with start (defensive checks will ensure cleanup)...")
            else:
                await send_deployment_update(server_id, "output", "Server stopped successfully, starting again...")
            
            # Add small delay to ensure cleanup
            await asyncio.sleep(0.5)
            
            success, message = await ssh_manager.start_server(server,
                                                             lambda msg: asyncio.create_task(
                                                                 send_deployment_update(server_id, "output", msg)
                                                             ))
            if success:
                server.status = ServerStatus.RUNNING
                log.status = "success"
                log.output = message
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
            # Store current status to restore after update
            current_status = server.status
            success, message = await ssh_manager.update_server(server,
                                                              lambda msg: asyncio.create_task(
                                                                  send_deployment_update(server_id, "output", msg)
                                                              ))
            
            if success:
                # Keep the same status as before update (or set to STOPPED if it was running)
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
            # Store current status to restore after validate
            current_status = server.status
            success, message = await ssh_manager.validate_server(server,
                                                                lambda msg: asyncio.create_task(
                                                                    send_deployment_update(server_id, "output", msg)
                                                                ))
            
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
            success, message = await ssh_manager.install_metamod(server,
                                                                lambda msg: asyncio.create_task(
                                                                    send_deployment_update(server_id, "output", msg)
                                                                ))
            
            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "Metamod installed successfully")
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"Metamod installation failed: {message}")
        
        elif action == "install_counterstrikesharp":
            await send_deployment_update(server_id, "status", "Installing CounterStrikeSharp...")
            success, message = await ssh_manager.install_counterstrikesharp(server,
                                                                           lambda msg: asyncio.create_task(
                                                                               send_deployment_update(server_id, "output", msg)
                                                                           ))
            
            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "CounterStrikeSharp installed successfully")
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"CounterStrikeSharp installation failed: {message}")
        
        elif action == "update_metamod":
            await send_deployment_update(server_id, "status", "Updating Metamod:Source...")
            success, message = await ssh_manager.update_metamod(server,
                                                               lambda msg: asyncio.create_task(
                                                                   send_deployment_update(server_id, "output", msg)
                                                               ))
            
            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "Metamod updated successfully")
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"Metamod update failed: {message}")
        
        elif action == "update_counterstrikesharp":
            await send_deployment_update(server_id, "status", "Updating CounterStrikeSharp...")
            success, message = await ssh_manager.update_counterstrikesharp(server,
                                                                          lambda msg: asyncio.create_task(
                                                                              send_deployment_update(server_id, "output", msg)
                                                                          ))
            
            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "CounterStrikeSharp updated successfully")
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"CounterStrikeSharp update failed: {message}")
        
        elif action == "install_cs2fixes":
            await send_deployment_update(server_id, "status", "Installing CS2Fixes...")
            success, message = await ssh_manager.install_cs2fixes(server,
                                                                 lambda msg: asyncio.create_task(
                                                                     send_deployment_update(server_id, "output", msg)
                                                                 ))
            
            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "CS2Fixes installed successfully")
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"CS2Fixes installation failed: {message}")
        
        elif action == "update_cs2fixes":
            await send_deployment_update(server_id, "status", "Updating CS2Fixes...")
            success, message = await ssh_manager.update_cs2fixes(server,
                                                                lambda msg: asyncio.create_task(
                                                                    send_deployment_update(server_id, "output", msg)
                                                                ))
            
            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "CS2Fixes updated successfully")
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"CS2Fixes update failed: {message}")
        
        elif action == "backup_plugins":
            await send_deployment_update(server_id, "status", "Backing up plugins...")
            success, message = await ssh_manager.backup_plugins(server,
                                                               lambda msg: asyncio.create_task(
                                                                   send_deployment_update(server_id, "output", msg)
                                                               ))
            
            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "Plugins backed up successfully")
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
            
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg
            )
        
        await db.commit()
        await db.refresh(server)
        await db.refresh(log)
        
        # Update cache
        await redis_manager.set_server_status(server_id, server.status.value)
        
        return ActionResponse(
            success=success,
            message=message,
            data={"status": server.status.value}
        )
    
    except Exception as e:
        log.status = "failed"
        log.error_message = str(e)
        server.status = ServerStatus.ERROR
        await db.commit()
        
        await send_deployment_update(server_id, "error", f"Action failed: {str(e)}")
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Action failed: {str(e)}"
        )


@router.get("/servers/{server_id}/deployment-progress")
async def get_deployment_progress(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get accumulated deployment progress for a server
    
    This endpoint allows clients to retrieve deployment progress after reconnecting
    or if the WebSocket connection was lost. Useful for recovering progress after
    program restart or SSH disconnect.
    """
    server = await get_server_and_verify_ownership(db, server_id, current_user.id)
    
    # Get accumulated progress from Redis
    progress = await redis_manager.get_deployment_progress(server_id)
    
    return {
        "server_id": server_id,
        "progress_messages": progress,
        "total_messages": len(progress)
    }


@router.get("/servers/{server_id}/logs", response_model=List[DeploymentLogResponse])
async def get_server_logs(
    server_id: int,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get deployment logs for a server"""
    server = await get_server_and_verify_ownership(db, server_id, current_user.id)
    
    logs = await DeploymentLog.get_logs_by_server(db, server_id, skip, limit)
    
    return logs


async def execute_single_server_action(server_id: int, action: str, user_id: int, batch_id: str):
    """
    Execute an action on a single server in the background.
    This function is designed to run as a background task.
    
    Args:
        server_id: Server ID
        action: Action to perform (restart, stop, update)
        user_id: User ID for ownership verification
        batch_id: Batch ID for tracking progress
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Update status to in_progress
        await redis_manager.set_batch_action_status(batch_id, server_id, "in_progress", "Starting...")
        
        # Get server and verify ownership - close DB session quickly to avoid pool exhaustion
        async with async_session_maker() as db:
            server = await Server.get_by_id_and_user(db, server_id, user_id)
            
            if not server:
                await redis_manager.set_batch_action_status(batch_id, server_id, "failed", "Server not found")
                return
        
        # DB session closed here - perform SSH operations without holding DB connection
        ssh_manager = SSHManager()
        success = False
        message = ""
        new_status = None
        
        try:
            if action == "restart":
                # Stop then start
                await redis_manager.set_batch_action_status(batch_id, server_id, "in_progress", "Stopping server...")
                stop_success, stop_msg = await ssh_manager.stop_server(server)
                
                # Add small delay
                await asyncio.sleep(0.5)
                
                await redis_manager.set_batch_action_status(batch_id, server_id, "in_progress", "Starting server...")
                success, message = await ssh_manager.start_server(server)
                
                if success:
                    new_status = ServerStatus.RUNNING
                else:
                    new_status = ServerStatus.ERROR
                    
            elif action == "stop":
                success, message = await ssh_manager.stop_server(server)
                if success:
                    new_status = ServerStatus.STOPPED
                else:
                    new_status = ServerStatus.ERROR
                    
            elif action == "update":
                await redis_manager.set_batch_action_status(batch_id, server_id, "in_progress", "Updating server...")
                success, message = await ssh_manager.update_server(server)
                if not success:
                    new_status = ServerStatus.ERROR
            
            # Update server status and create deployment log in a separate quick session
            async with async_session_maker() as db:
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
                    error_message=message if not success else None
                )
                db.add(log)
                await db.commit()
            
            # Update final status
            if success:
                await redis_manager.set_batch_action_status(batch_id, server_id, "success", message)
            else:
                await redis_manager.set_batch_action_status(batch_id, server_id, "failed", message)
                
        except Exception as e:
            logger.error(f"Error executing action {action} on server {server_id}: {e}")
            await redis_manager.set_batch_action_status(batch_id, server_id, "failed", str(e))
            
    except Exception as e:
        logger.error(f"Background task error for server {server_id}: {e}")
        await redis_manager.set_batch_action_status(batch_id, server_id, "failed", str(e))


@router.post("/servers/batch-actions", response_model=BatchActionResponse)
async def batch_server_actions(
    request: BatchActionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Execute an action on multiple servers asynchronously (non-blocking).
    
    This endpoint immediately returns after validating the request and spawning
    background tasks for each server. The web UI will not block while waiting
    for operations to complete.
    
    Use the batch_id returned to check progress via GET /servers/batch-actions/{batch_id}
    
    Args:
        request: BatchActionRequest with server_ids and action
    
    Returns:
        BatchActionResponse with batch_id for tracking progress
    """
    # Generate cryptographically secure batch ID (16 bytes = 32 hex chars)
    batch_id = secrets.token_hex(16)
    
    # Validate all servers exist and belong to current user
    valid_server_ids = []
    for server_id in request.server_ids:
        server = await db.get(Server, server_id)
        
        if server and server.user_id == current_user.id:
            valid_server_ids.append(server_id)
            # Set initial status as pending
            await redis_manager.set_batch_action_status(batch_id, server_id, "pending", "Queued for processing")
    
    if not valid_server_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid servers found in the request"
        )
    
    # Spawn background tasks for each server - these run in parallel
    # Tasks are stored to prevent garbage collection
    for server_id in valid_server_ids:
        task = asyncio.create_task(
            execute_single_server_action(server_id, request.action, current_user.id, batch_id)
        )
        _store_task(task)
    
    return BatchActionResponse(
        success=True,
        message=f"Batch action '{request.action}' started for {len(valid_server_ids)} server(s)",
        batch_id=batch_id,
        server_count=len(valid_server_ids)
    )


@router.get("/servers/batch-actions/{batch_id}")
async def get_batch_action_status(
    batch_id: str,
    current_user: User = Depends(get_current_active_user)
):
    """
    Get the status of a batch action.
    
    Args:
        batch_id: The batch ID returned from the batch-actions endpoint
    
    Returns:
        Status of each server in the batch
    """
    statuses = await redis_manager.get_batch_action_status(batch_id)
    
    if not statuses:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Batch action not found or expired"
        )
    
    # Calculate summary
    total = len(statuses)
    completed = sum(1 for s in statuses.values() if s.get("status") in ["success", "failed"])
    succeeded = sum(1 for s in statuses.values() if s.get("status") == "success")
    failed = sum(1 for s in statuses.values() if s.get("status") == "failed")
    in_progress = sum(1 for s in statuses.values() if s.get("status") in ["pending", "in_progress"])
    
    return {
        "batch_id": batch_id,
        "servers": statuses,
        "summary": {
            "total": total,
            "completed": completed,
            "succeeded": succeeded,
            "failed": failed,
            "in_progress": in_progress,
            "is_complete": completed == total
        }
    }


async def execute_single_server_plugins(server_id: int, plugins: List[str], user_id: int, batch_id: str):
    """
    Install plugins on a single server in the background.
    This function is designed to run as a background task.
    
    Args:
        server_id: Server ID
        plugins: List of plugins to install
        user_id: User ID for ownership verification
        batch_id: Batch ID for tracking progress
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Update status to in_progress
        await redis_manager.set_batch_action_status(batch_id, server_id, "in_progress", "Starting plugin installation...")
        
        # Get server and verify ownership - close DB session quickly to avoid pool exhaustion
        async with async_session_maker() as db:
            server = await Server.get_by_id_and_user(db, server_id, user_id)
            
            if not server:
                await redis_manager.set_batch_action_status(batch_id, server_id, "failed", "Server not found")
                return
        
        # DB session closed here - perform SSH operations without holding DB connection
        ssh_manager = SSHManager()
        plugin_results = []
        
        for plugin in plugins:
            try:
                await redis_manager.set_batch_action_status(batch_id, server_id, "in_progress", f"Installing {plugin}...")
                
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
                
                # Create deployment log in a separate quick session
                async with async_session_maker() as db:
                    log = DeploymentLog(
                        server_id=server_id,
                        action=f"install_{plugin}",
                        status="success" if success else "failed",
                        output=message if success else None,
                        error_message=message if not success else None
                    )
                    db.add(log)
                    await db.commit()
                
                plugin_results.append({
                    "plugin": plugin,
                    "success": success,
                    "message": message
                })
                
            except Exception as e:
                logger.error(f"Error installing {plugin} on server {server_id}: {e}")
                plugin_results.append({
                    "plugin": plugin,
                    "success": False,
                    "message": str(e)
                })
        
        # Determine overall success
        overall_success = all(r["success"] for r in plugin_results)
        summary = ", ".join([f"{r['plugin']}: {'✓' if r['success'] else '✗'}" for r in plugin_results])
        
        if overall_success:
            await redis_manager.set_batch_action_status(batch_id, server_id, "success", summary)
        else:
            await redis_manager.set_batch_action_status(batch_id, server_id, "failed", summary)
                
    except Exception as e:
        logger.error(f"Background task error for server {server_id}: {e}")
        await redis_manager.set_batch_action_status(batch_id, server_id, "failed", str(e))


@router.post("/servers/batch-install-plugins", response_model=BatchActionResponse)
async def batch_install_plugins(
    request: BatchInstallPluginsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Install plugins on multiple servers asynchronously (non-blocking).
    
    This endpoint immediately returns after validating the request and spawning
    background tasks for each server. The web UI will not block while waiting
    for operations to complete.
    
    Use the batch_id returned to check progress via GET /servers/batch-actions/{batch_id}
    
    Args:
        request: BatchInstallPluginsRequest with server_ids and plugins
    
    Returns:
        BatchActionResponse with batch_id for tracking progress
    """
    # Generate cryptographically secure batch ID (16 bytes = 32 hex chars)
    batch_id = secrets.token_hex(16)
    
    # Validate all servers exist and belong to current user
    valid_server_ids = []
    for server_id in request.server_ids:
        server = await db.get(Server, server_id)
        
        if server and server.user_id == current_user.id:
            valid_server_ids.append(server_id)
            # Set initial status as pending
            await redis_manager.set_batch_action_status(batch_id, server_id, "pending", "Queued for plugin installation")
    
    if not valid_server_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid servers found in the request"
        )
    
    # Spawn background tasks for each server - these run in parallel
    # Tasks are stored to prevent garbage collection
    for server_id in valid_server_ids:
        task = asyncio.create_task(
            execute_single_server_plugins(server_id, request.plugins, current_user.id, batch_id)
        )
        _store_task(task)
    
    plugins_str = ", ".join(request.plugins)
    return BatchActionResponse(
        success=True,
        message=f"Installing {plugins_str} on {len(valid_server_ids)} server(s) in background",
        batch_id=batch_id,
        server_count=len(valid_server_ids)
    )


@router.websocket("/servers/{server_id}/ssh-console")
async def ssh_console_websocket(websocket: WebSocket, server_id: int):
    """
    WebSocket endpoint for SSH console access
    Provides interactive SSH terminal access to the server
    """
    await websocket.accept()
    
    try:
        # Get server details from database
        from modules.database import async_session_maker
        async with async_session_maker() as db:
            server = await db.get(Server, server_id)
            
            if not server:
                await websocket.send_json({
                    "type": "error",
                    "message": "Server not found"
                })
                await websocket.close()
                return
        
        # Create SSH connection
        ssh_manager = SSHManager()
        success, msg = await ssh_manager.connect(server)
        
        if not success:
            await websocket.send_json({
                "type": "error",
                "message": f"SSH connection failed: {msg}"
            })
            await websocket.close()
            return
        
        await websocket.send_json({
            "type": "connected",
            "message": f"Connected to {server.host}"
        })
        
        # Handle interactive shell
        try:
            # Create interactive process with PTY for interactive shell
            # Request a PTY to enable interactive terminal features
            process = await ssh_manager.conn.create_process(
                term_type='xterm-256color',
                encoding='utf-8',
                errors='replace'
            )
            
            async def read_output():
                """Read output from SSH and send to WebSocket"""
                try:
                    while True:
                        output = await process.stdout.read(1024)
                        if output:
                            await websocket.send_json({
                                "type": "output",
                                "data": output
                            })
                        else:
                            break
                except Exception as e:
                    pass
            
            # Start reading output
            output_task = asyncio.create_task(read_output())
            
            # Handle input from WebSocket
            while True:
                data = await websocket.receive_text()
                message = json.loads(data)
                
                if message.get("type") == "input":
                    # Send input to SSH
                    input_data = message.get("data", "")
                    process.stdin.write(input_data)
                    await process.stdin.drain()
                elif message.get("type") == "resize":
                    # Handle terminal resize
                    cols = message.get("cols", 80)
                    rows = message.get("rows", 24)
                    process.change_terminal_size(cols, rows)
                elif message.get("type") == "disconnect":
                    break
        
        except Exception as e:
            await websocket.send_json({
                "type": "error",
                "message": f"Console error: {str(e)}"
            })
        finally:
            await ssh_manager.disconnect()
    
    except WebSocketDisconnect:
        pass


@router.websocket("/servers/{server_id}/game-console")
async def game_console_websocket(websocket: WebSocket, server_id: int):
    """
    WebSocket endpoint for game console access
    Uses interactive PTY attachment to screen session for real-time console access (like screen -x)
    Same logic as SSH console but attaches to the game server screen session
    """
    await websocket.accept()
    
    try:
        # Get server details from database
        from modules.database import async_session_maker
        async with async_session_maker() as db:
            server = await db.get(Server, server_id)
            
            if not server:
                await websocket.send_json({
                    "type": "error",
                    "message": "Server not found"
                })
                await websocket.close()
                return
        
        # Create SSH connection using SSHManager (same as SSH console)
        ssh_manager = SSHManager()
        success, msg = await ssh_manager.connect(server)
        
        if not success:
            await websocket.send_json({
                "type": "error",
                "message": f"SSH connection failed: {msg}"
            })
            await websocket.close()
            return
        
        # Check if game server is running
        screen_name = f"cs2server_{server_id}"
        try:
            success, stdout, stderr = await ssh_manager.execute_command(
                f"screen -list | grep {screen_name} || true",
                timeout=10
            )
            if not stdout or not stdout.strip() or screen_name not in stdout:
                await websocket.send_json({
                    "type": "error",
                    "message": "Game server is not running. Please start the server first."
                })
                await websocket.close()
                await ssh_manager.disconnect()
                return
        except Exception as e:
            await websocket.send_json({
                "type": "error",
                "message": f"Failed to check server status: {str(e)}"
            })
            await websocket.close()
            await ssh_manager.disconnect()
            return
        
        await websocket.send_json({
            "type": "connected",
            "message": f"Connected to CS2 server console on {server.host}"
        })
        
        # Attach to screen session with PTY (same logic as SSH console)
        try:
            # Create interactive process with PTY to attach to screen session
            # screen -x allows multiple users to attach to the same session
            process = await ssh_manager.conn.create_process(
                f"screen -x {screen_name}",
                term_type='xterm-256color',
                encoding='utf-8',
                errors='replace'
            )
        
            async def read_output():
                """Read output from screen session and send to WebSocket"""
                try:
                    while True:
                        output = await process.stdout.read(1024)
                        if output:
                            await websocket.send_json({
                                "type": "output",
                                "data": output
                            })
                        else:
                            break
                except Exception as e:
                    pass
            
            # Start reading output
            output_task = asyncio.create_task(read_output())
            
            # Handle input from WebSocket
            while True:
                data = await websocket.receive_text()
                message = json.loads(data)
                
                if message.get("type") == "input":
                    # Send input directly to screen session via stdin
                    input_data = message.get("data", "")
                    process.stdin.write(input_data)
                    await process.stdin.drain()
                elif message.get("type") == "resize":
                    # Handle terminal resize
                    cols = message.get("cols", 80)
                    rows = message.get("rows", 24)
                    process.change_terminal_size(cols, rows)
                elif message.get("type") == "ping":
                    # Respond to ping to keep connection alive
                    try:
                        await websocket.send_json({
                            "type": "pong"
                        })
                    except Exception:
                        break
                elif message.get("type") == "disconnect":
                    break
        
        except Exception as e:
            await websocket.send_json({
                "type": "error",
                "message": f"Console error: {str(e)}"
            })
        finally:
            await ssh_manager.disconnect()
    
    except WebSocketDisconnect:
        pass



@router.get("/servers/{server_id}/ssh-connection-info")
async def get_ssh_connection_info(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get SSH connection information for a server.
    Returns connection status, age, reconnection count, and pooling status.
    """
    # Get server and verify ownership
    server = await get_server_and_verify_ownership(db, server_id, current_user.id)
    
    # Get connection info from pool
    from services.ssh_connection_pool import ssh_connection_pool
    
    connection_info = await ssh_connection_pool.get_connection_info(server)
    
    return connection_info


@router.post("/servers/{server_id}/reconnect-ssh")
async def reconnect_ssh(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Manually reconnect SSH connection for a server.
    This bypasses rate limiting, resets the reconnection counter, and clears the SSH down flag.
    """
    from sqlalchemy import update as sql_update
    
    # Get server and verify ownership
    server = await get_server_and_verify_ownership(db, server_id, current_user.id)
    
    # Clear the SSH down flag to allow reconnection
    if server.is_ssh_down:
        await db.execute(
            sql_update(Server)
            .where(Server.id == server_id)
            .values(
                is_ssh_down=False,
                consecutive_ssh_failures=0
            )
        )
        await db.commit()
        await db.refresh(server)
    
    # Perform manual reconnection through pool
    from services.ssh_connection_pool import ssh_connection_pool
    
    try:
        success, conn, msg = await ssh_connection_pool.manual_reconnect(server)
        if success:
            return {
                "success": True,
                "message": msg
            }
        else:
            return {
                "success": False,
                "message": msg
            }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reconnect: {str(e)}"
        )


@router.post("/servers/{server_id}/reset-reconnect-counter")
async def reset_reconnect_counter(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Reset the reconnection counter for a server without reconnecting.
    """
    # Get server and verify ownership
    server = await get_server_and_verify_ownership(db, server_id, current_user.id)
    
    # Reset counter through pool
    from services.ssh_connection_pool import ssh_connection_pool
    
    try:
        await ssh_connection_pool.reset_reconnection_counter(server)
        return {
            "success": True,
            "message": "重连计数已重置 | Reconnection counter reset"
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset counter: {str(e)}"
        )
