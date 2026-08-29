"""Lock checks that allow FIFO queueing when a hub job is already active."""

from __future__ import annotations

from fastapi import HTTPException, status

from services.maintenance_lock import maintenance_lock_service
from services.redis_manager import redis_manager
from services.server_operation_hub import ACTIVE_STATUSES, server_operation_hub


async def has_active_operation(server_id: int) -> bool:
    current = await server_operation_hub.get_current(server_id)
    return bool(current and current.get("status") in ACTIVE_STATUSES)


async def reject_stuck_lock_unless_active(server_id: int) -> None:
    """409 only when a lock is held and no hub job is running or queued.

    An active hub operation already serializes the server. New submits should
    join the FIFO instead of failing.
    """
    if await has_active_operation(server_id):
        return
    if await redis_manager.get(f"deployment_lock:{server_id}"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Server is currently being deployed or has a stuck deployment lock. "
                "Clear the lock before starting another operation."
            ),
        )
    if await maintenance_lock_service.is_locked(server_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another operation already holds the server lock.",
        )
