"""Actions batch endpoints."""

# ruff: noqa: F403,F405

from sqlmodel import select

from .common import *

router = APIRouter(tags=["actions"])


async def _get_owned_server_ids(
    db: AsyncSession, requested_server_ids: list[int], user_id: int
) -> list[int]:
    """Validate a batch with one query while preserving request order."""
    unique_server_ids = list(dict.fromkeys(requested_server_ids))
    if not unique_server_ids:
        return []

    result = await db.execute(
        select(Server.id).where(
            Server.id.in_(unique_server_ids),
            Server.user_id == user_id,
        )
    )
    owned_server_ids = set(result.scalars().all())
    return [server_id for server_id in unique_server_ids if server_id in owned_server_ids]


@router.post("/servers/batch-actions", response_model=BatchActionResponse)
async def batch_server_actions(
    request: BatchActionRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    lock_service: MaintenanceLockService = Depends(resolve_maintenance_lock_service),
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
    valid_server_ids = await _get_owned_server_ids(db, request.server_ids, current_user.id)

    if not valid_server_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No valid servers found in the request"
        )

    await db.commit()
    await redis_manager.initialize_batch_action(
        batch_id,
        current_user.id,
        valid_server_ids,
        "pending",
        "Queued for processing",
    )
    await _reserve_batch_capacity(current_user.id, len(valid_server_ids))
    session_factory = _request_session_factory(http_request)
    # Spawn background tasks for each server - these run in parallel
    # Tasks are stored to prevent garbage collection
    for server_id in valid_server_ids:
        _spawn_action_task(
            http_request,
            _run_bounded_batch_operation(
                server_id,
                current_user.id,
                batch_id,
                f"batch_action:{request.action}",
                _bind_maintenance_lock(
                    lambda server_id=server_id: execute_single_server_action(
                        server_id,
                        request.action,
                        current_user.id,
                        current_user.is_admin,
                        batch_id,
                        session_factory=session_factory,
                    ),
                    lock_service,
                ),
            ),
            name=f"batch-action-{batch_id}-{server_id}",
        )

    return BatchActionResponse(
        success=True,
        message=f"Batch action '{request.action}' started for {len(valid_server_ids)} server(s)",
        batch_id=batch_id,
        server_count=len(valid_server_ids),
    )


@router.get("/servers/batch-actions/{batch_id}")
async def get_batch_action_status(
    batch_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Get the status of a batch action.

    Args:
        batch_id: The batch ID returned from the batch-actions endpoint

    Returns:
        Status of each server in the batch
    """
    statuses = await redis_manager.get_batch_action_status(batch_id, user_id=current_user.id)

    # During the one-hour migration window, old per-server keys do not carry an
    # owner. Preserve access only after proving every referenced server belongs
    # to the requesting user in a single query.
    if not statuses:
        legacy_statuses = await redis_manager.get_legacy_batch_action_status(batch_id)
        try:
            legacy_server_ids = [int(server_id) for server_id in legacy_statuses]
        except ValueError:
            legacy_server_ids = []
        if legacy_server_ids:
            owned_server_ids = await _get_owned_server_ids(db, legacy_server_ids, current_user.id)
            if set(owned_server_ids) == set(legacy_server_ids):
                statuses = legacy_statuses

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
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    lock_service: MaintenanceLockService = Depends(resolve_maintenance_lock_service),
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
    valid_server_ids = await _get_owned_server_ids(db, request.server_ids, current_user.id)

    if not valid_server_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No valid servers found in the request"
        )

    await db.commit()
    await redis_manager.initialize_batch_action(
        batch_id,
        current_user.id,
        valid_server_ids,
        "pending",
        "Queued for plugin installation",
    )
    await _reserve_batch_capacity(current_user.id, len(valid_server_ids))
    session_factory = _request_session_factory(http_request)
    # Spawn background tasks for each server - these run in parallel
    # Tasks are stored to prevent garbage collection
    for server_id in valid_server_ids:
        _spawn_action_task(
            http_request,
            _run_bounded_batch_operation(
                server_id,
                current_user.id,
                batch_id,
                "batch_plugin_install",
                _bind_maintenance_lock(
                    lambda server_id=server_id: execute_single_server_plugins(
                        server_id,
                        request.plugins,
                        current_user.id,
                        current_user.is_admin,
                        batch_id,
                        session_factory=session_factory,
                    ),
                    lock_service,
                ),
            ),
            name=f"batch-plugin-install-{batch_id}-{server_id}",
        )

    plugins_str = ", ".join(request.plugins)
    return BatchActionResponse(
        success=True,
        message=f"Installing {plugins_str} on {len(valid_server_ids)} server(s) in background",
        batch_id=batch_id,
        server_count=len(valid_server_ids),
    )


@router.post("/servers/batch-send-command", response_model=BatchActionResponse)
async def batch_send_command(
    request: BatchSendCommandRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    lock_service: MaintenanceLockService = Depends(resolve_maintenance_lock_service),
):
    """
    Send a command to multiple game servers asynchronously (non-blocking).

    This endpoint sends the specified command to all selected game servers.
    Commands are sent via each server's configured detached session.

    Use the batch_id returned to check progress via GET /servers/batch-actions/{batch_id}

    Args:
        request: BatchSendCommandRequest with server_ids and command

    Returns:
        BatchActionResponse with batch_id for tracking progress
    """
    # Generate cryptographically secure batch ID (16 bytes = 32 hex chars)
    batch_id = secrets.token_hex(16)

    # Validate all servers exist and belong to current user
    valid_server_ids = await _get_owned_server_ids(db, request.server_ids, current_user.id)

    if not valid_server_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No valid servers found in the request"
        )

    await db.commit()
    await redis_manager.initialize_batch_action(
        batch_id,
        current_user.id,
        valid_server_ids,
        "pending",
        "Queued for command execution",
    )
    await _reserve_batch_capacity(current_user.id, len(valid_server_ids))
    session_factory = _request_session_factory(http_request)
    # Spawn background tasks for each server - these run in parallel
    # Tasks are stored to prevent garbage collection
    for server_id in valid_server_ids:
        _spawn_action_task(
            http_request,
            _run_bounded_batch_operation(
                server_id,
                current_user.id,
                batch_id,
                "batch_command",
                _bind_maintenance_lock(
                    lambda server_id=server_id: execute_single_server_command(
                        server_id,
                        request.command,
                        current_user.id,
                        current_user.is_admin,
                        batch_id,
                        session_factory=session_factory,
                    ),
                    lock_service,
                ),
            ),
            name=f"batch-command-{batch_id}-{server_id}",
        )

    return BatchActionResponse(
        success=True,
        message=f"Sending command to {len(valid_server_ids)} server(s) in background",
        batch_id=batch_id,
        server_count=len(valid_server_ids),
    )
