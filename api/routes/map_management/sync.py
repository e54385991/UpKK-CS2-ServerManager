"""Scheduled custom map-pool sync helpers."""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from modules import ScheduledTask, Server
from services.compat import LateBoundModule

host = LateBoundModule("api.routes.map_management")


async def _get_map_sync_tasks(
    db: AsyncSession,
    server_id: int,
) -> list[ScheduledTask]:
    result = await db.execute(
        select(ScheduledTask)
        .where(
            ScheduledTask.server_id == server_id,
            ScheduledTask.action == host.MAP_POOL_SYNC_ACTION,
        )
        .order_by(col(ScheduledTask.id).asc())
    )
    return list(result.scalars().all())


def _map_sync_payload(server: Server, task: Optional[ScheduledTask]) -> dict[str, object]:
    try:
        interval_seconds = (
            int(task.schedule_value) if task else host.MAP_POOL_SYNC_MIN_INTERVAL_SECONDS
        )
    except TypeError, ValueError:
        interval_seconds = host.MAP_POOL_SYNC_MIN_INTERVAL_SECONDS
    return {
        "url": server.map_pool_sync_url or "",
        "enabled": bool(task and task.enabled),
        "interval_seconds": max(host.MAP_POOL_SYNC_MIN_INTERVAL_SECONDS, interval_seconds),
        "last_run": task.last_run if task else None,
        "next_run": task.next_run if task else None,
        "last_status": task.last_status if task else None,
        "last_error": task.last_error if task else None,
        "run_count": task.run_count if task else 0,
    }


async def _record_map_sync_result(
    db: AsyncSession,
    task: Optional[ScheduledTask],
    *,
    success: bool,
    error: Optional[str] = None,
) -> None:
    if task is None:
        return
    now = host.get_current_time()
    try:
        interval_seconds = max(host.MAP_POOL_SYNC_MIN_INTERVAL_SECONDS, int(task.schedule_value))
    except TypeError, ValueError:
        interval_seconds = host.MAP_POOL_SYNC_MIN_INTERVAL_SECONDS
    task.last_run = now
    task.last_status = "success" if success else "failed"
    task.last_error = error
    task.run_count += 1
    task.next_run = now + timedelta(seconds=interval_seconds) if task.enabled else None
    db.add(task)
    await db.commit()
