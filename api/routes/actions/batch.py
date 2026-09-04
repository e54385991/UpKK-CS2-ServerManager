"""Actions batch endpoints."""

# ruff: noqa: F403,F405

from fastapi import Request

from api.dependencies import ActiveUser, DatabaseSession
from api.routes.actions.common import (
    _reserve_batch_capacity,
    _run_bounded_batch_operation,
    _store_task,
)
from services.audit_log_service import record_audit_event
from services.servers.batch import authorized_server_ids

from .common import *

router = APIRouter(tags=["actions"])


@router.post("/servers/batch-actions", response_model=BatchActionResponse)
async def batch_server_actions(
    request: BatchActionRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    http_request: Request,
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

    valid_server_ids = await authorized_server_ids(db, request.server_ids, current_user.id)

    if not valid_server_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No valid servers found in the request"
        )

    await redis_manager.set_batch_action_statuses(
        batch_id,
        valid_server_ids,
        "pending",
        "Queued for processing",
    )

    await _reserve_batch_capacity(current_user.id, len(valid_server_ids))
    # Spawn background tasks for each server - these run in parallel
    # Tasks are stored to prevent garbage collection
    for server_id in valid_server_ids:
        task = asyncio.create_task(
            _run_bounded_batch_operation(
                server_id,
                current_user.id,
                batch_id,
                f"batch_action:{request.action}",
                lambda server_id=server_id: execute_single_server_action(
                    server_id, request.action, current_user.id, current_user.is_admin, batch_id
                ),
                acquire_lock=False,
            )
        )
        _store_task(task)

    await record_audit_event(
        category="server",
        action=f"server.batch.{request.action}",
        status="requested",
        user=current_user,
        request=http_request,
        details={"server_ids": valid_server_ids, "batch_id": batch_id},
    )
    return BatchActionResponse(
        success=True,
        message=f"Batch action '{request.action}' started for {len(valid_server_ids)} server(s)",
        batch_id=batch_id,
        server_count=len(valid_server_ids),
    )


@router.get("/servers/batch-actions/{batch_id}")
async def get_batch_action_status(batch_id: str, current_user: ActiveUser):
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
            status_code=status.HTTP_404_NOT_FOUND, detail="Batch action not found or expired"
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
            "is_complete": completed == total,
        },
    }


@router.post("/servers/batch-install-plugins", response_model=BatchActionResponse)
async def batch_install_plugins(
    request: BatchInstallPluginsRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    http_request: Request,
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

    valid_server_ids = await authorized_server_ids(db, request.server_ids, current_user.id)

    if not valid_server_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No valid servers found in the request"
        )

    await redis_manager.set_batch_action_statuses(
        batch_id,
        valid_server_ids,
        "pending",
        "Queued for plugin installation",
    )

    await _reserve_batch_capacity(current_user.id, len(valid_server_ids))
    # Spawn background tasks for each server - these run in parallel
    # Tasks are stored to prevent garbage collection
    for server_id in valid_server_ids:
        task = asyncio.create_task(
            _run_bounded_batch_operation(
                server_id,
                current_user.id,
                batch_id,
                "batch_plugin_install",
                lambda server_id=server_id: execute_single_server_plugins(
                    server_id, request.plugins, current_user.id, current_user.is_admin, batch_id
                ),
                acquire_lock=False,
            )
        )
        _store_task(task)

    plugins_str = ", ".join(request.plugins)
    await record_audit_event(
        category="server",
        action="server.batch.install_plugins",
        status="requested",
        user=current_user,
        request=http_request,
        details={
            "server_ids": valid_server_ids,
            "plugins": request.plugins,
            "batch_id": batch_id,
        },
    )
    return BatchActionResponse(
        success=True,
        message=f"Installing {plugins_str} on {len(valid_server_ids)} server(s) in background",
        batch_id=batch_id,
        server_count=len(valid_server_ids),
    )


@router.post("/servers/batch-send-command", response_model=BatchActionResponse)
async def batch_send_command(
    request: BatchSendCommandRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    http_request: Request,
):
    """
    Queue a command for multiple game servers asynchronously (non-blocking).

    Each server command is placed on that server's task queue before it is
    sent via the configured detached session.

    Use the batch_id returned to check progress via GET /servers/batch-actions/{batch_id}

    Args:
        request: BatchSendCommandRequest with server_ids and command

    Returns:
        BatchActionResponse with batch_id for tracking progress
    """
    # Generate cryptographically secure batch ID (16 bytes = 32 hex chars)
    batch_id = secrets.token_hex(16)

    valid_server_ids = await authorized_server_ids(db, request.server_ids, current_user.id)

    if not valid_server_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No valid servers found in the request"
        )

    await redis_manager.set_batch_action_statuses(
        batch_id,
        valid_server_ids,
        "pending",
        "Queued for command execution",
    )

    await _reserve_batch_capacity(current_user.id, len(valid_server_ids))
    # Spawn background tasks for each server - these run in parallel
    # Tasks are stored to prevent garbage collection
    for server_id in valid_server_ids:
        task = asyncio.create_task(
            _run_bounded_batch_operation(
                server_id,
                current_user.id,
                batch_id,
                "batch_command",
                lambda server_id=server_id: execute_single_server_command(
                    server_id, request.command, current_user.id, current_user.is_admin, batch_id
                ),
                acquire_lock=False,
            )
        )
        _store_task(task)

    await record_audit_event(
        category="server",
        action="server.batch.send_command",
        status="requested",
        user=current_user,
        request=http_request,
        details={
            "server_ids": valid_server_ids,
            "command_present": True,
            "batch_id": batch_id,
        },
    )
    return BatchActionResponse(
        success=True,
        message=f"Sending command to {len(valid_server_ids)} server(s) in background",
        batch_id=batch_id,
        server_count=len(valid_server_ids),
    )
