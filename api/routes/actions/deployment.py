"""Actions deployment endpoints."""

# ruff: noqa: F403,F405

from fastapi import Request as Request

from api.dependencies import ActiveUser as ActiveUser
from api.dependencies import DatabaseSession as DatabaseSession
from api.routes.actions.common import _store_task as _store_task
from services.audit_log_service import record_audit_event as record_audit_event
from services.maintenance_lock import maintenance_lock_service as maintenance_lock_service
from services.restart_protection import (
    clear_operator_crash_protection as clear_operator_crash_protection,
)
from services.server_compatibility import (
    maybe_clear_execstack_after_file_action as maybe_clear_execstack_after_file_action,
)
from services.server_compatibility import run_clear_execstack as run_clear_execstack
from services.steamcmd_guard import STEAMCMD_ACTIONS as STEAMCMD_ACTIONS
from services.steamcmd_guard import STEAMCMD_FORCE_TERMINATED as STEAMCMD_FORCE_TERMINATED
from services.steamcmd_guard import clear_steamcmd_cancel as clear_steamcmd_cancel
from services.steamcmd_guard import force_clear_steamcmd_lock as force_clear_steamcmd_lock
from services.steamcmd_guard import prepare_steamcmd_operation as prepare_steamcmd_operation
from services.steamcmd_guard import request_steamcmd_cancel as request_steamcmd_cancel

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
        accumulated_progress = await redis_manager.get_deployment_progress(server_id)
        if accumulated_progress:
            await websocket.send_json(
                {
                    "type": "info",
                    "message": f"Recovered {len(accumulated_progress)} progress message(s) from previous session",
                    "timestamp": get_current_time().isoformat(),
                }
            )
            for progress_entry in accumulated_progress:
                await websocket.send_json(progress_entry)

        while True:
            await websocket.receive_text()
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
        raise
    except Exception as e:
        logger.error(f"Error clearing deployment lock for server {server_id}: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"Failed to clear deployment lock: {str(e)}"},
        )


from .deployment_executor import execute_server_action as execute_server_action  # noqa: E402


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
