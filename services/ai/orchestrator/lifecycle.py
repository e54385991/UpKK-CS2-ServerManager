"""AI run expiry, lock reconciliation, and interruption."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from modules.models import AIRun, AIToolRun, User
from services.compat import LateBoundModule

host = LateBoundModule("services.ai_orchestrator")


async def cleanup_expired_ai_runs(
    db,
    *,
    user_id: int | None = None,
) -> int:
    """Delete terminal run metadata after the background-task visibility window."""
    cutoff = host.get_current_time() - timedelta(minutes=host.AI_BACKGROUND_TASK_RETENTION_MINUTES)
    filters = [
        col(AIRun.status).in_(host.TERMINAL_RUN_STATUSES),
        func.coalesce(AIRun.completed_at, AIRun.updated_at, AIRun.created_at) < cutoff,
    ]
    if user_id is not None:
        filters.append(AIRun.user_id == user_id)
    result = await db.execute(select(AIRun).where(*filters))
    runs = list(result.scalars().all())
    for run in runs:
        await db.delete(run)
    if runs:
        await db.commit()
    return len(runs)


async def reconcile_stale_ai_server_lock(db, server_id: int | None) -> bool:
    """Release an AI lock only when no persisted AI run still targets it."""
    if server_id is None:
        return False
    result = await db.execute(
        select(AIRun.id)
        .where(
            AIRun.server_id == server_id,
            col(AIRun.status).in_(host.ACTIVE_RUN_STATUSES),
        )
        .limit(1)
    )
    if result.scalar_one_or_none() is not None:
        return False
    legacy_cutoff = host.get_current_time() - timedelta(
        minutes=host.AI_BACKGROUND_TASK_RETENTION_MINUTES
    )
    legacy_result = await db.execute(
        select(AIToolRun.id)
        .join(AIRun, col(AIToolRun.run_id) == col(AIRun.id))
        .where(
            AIRun.server_id == server_id,
            col(AIRun.status).in_(host.TERMINAL_RUN_STATUSES),
            col(AIToolRun.tool_name).in_(host.AI_LEGACY_WRITE_TOOLS),
            func.coalesce(AIToolRun.completed_at, AIToolRun.created_at) >= legacy_cutoff,
        )
        .limit(1)
    )
    operation_prefixes = host.AI_SERVER_LOCK_OPERATION_PREFIXES
    if legacy_result.scalar_one_or_none() is not None:
        operation_prefixes += (
            "plugin_install_plan",
            "github_plugin_install_plan",
            "workshop_map_plan",
        )
    return await host.maintenance_lock_service.clear_stale_server_lock(
        server_id,
        operation_prefixes=operation_prefixes,
    )


async def interrupt_active_ai_runs() -> int:
    """Mark non-terminal work interrupted after an application restart."""
    async with host.async_session_maker() as db:
        result = await db.execute(
            select(AIRun).where(col(AIRun.status).in_(host.ACTIVE_RUN_STATUSES))
        )
        runs = list(result.scalars().all())
        user_ids = {run.user_id for run in runs}
        server_ids = {run.server_id for run in runs if run.server_id is not None}
        if runs:
            tool_result = await db.execute(
                select(AIToolRun).where(
                    col(AIToolRun.run_id).in_([run.id for run in runs]),
                    col(AIToolRun.status).in_(
                        ("pending", "pending_approval", "approved", "queued", "running")
                    ),
                )
            )
            tools_by_run: dict[str, list[AIToolRun]] = {}
            for item in tool_result.scalars().all():
                tools_by_run.setdefault(item.run_id, []).append(item)
            for run in runs:
                await host._close_unexecuted_tools(
                    db,
                    run,
                    tools_by_run.get(run.id, []),
                    expired_ids=set(),
                    cancellation_error="Application restarted before this tool completed",
                    cancellation_status="interrupted",
                )
        for run in runs:
            run.status = "interrupted"
            run.error = "Application restarted before this run completed"
            run.completed_at = host.get_current_time()
            db.add(run)
            host._add_run_error_message(db, run, run.error)
        await db.commit()
    # Release stale AI write locks left behind by the previous process.
    for uid in user_ids:
        key = host.redis_manager.prefixed_key(f"server_operation_lock:-{uid + 1}")
        try:
            await host.redis_manager.client.delete(key)
        except Exception:
            pass
    for server_id in server_ids:
        await host.maintenance_lock_service.force_release_server_lock(server_id)
    return len(runs)


async def interrupt_conversation_run(
    db: AsyncSession, user: User, conversation_id: str
) -> dict[str, Any]:
    """Force-stop the active AI run for a conversation so the user can send a new message."""
    result = await db.execute(
        select(AIRun).where(
            AIRun.conversation_id == conversation_id,
            col(AIRun.status).in_(host.ACTIVE_RUN_STATUSES),
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        return {"interrupted": False, "message": "No active run found for this conversation"}
    if run.user_id != user.id and not user.is_admin:
        raise PermissionError("Only the conversation owner or an admin can interrupt this run")
    if run.status == "waiting_approval":
        tool_result = await db.execute(
            select(AIToolRun).where(
                AIToolRun.run_id == run.id,
                col(AIToolRun.status).in_(("pending_approval", "approved", "queued")),
            )
        )
        await host._close_unexecuted_tools(
            db,
            run,
            list(tool_result.scalars().all()),
            expired_ids=set(),
            cancellation_error="Interrupted by user before approval execution",
        )
    run.status = "interrupted"
    run.error = "Interrupted by user"
    run.completed_at = host.get_current_time()
    db.add(run)
    host._add_run_error_message(db, run, run.error)
    await db.commit()
    await host._emit(run.id, "run_interrupted", {"error": "Interrupted by user"})
    return {"interrupted": True, "run_id": run.id}
