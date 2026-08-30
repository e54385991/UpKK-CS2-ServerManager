"""Versioned scheduled-task workspace for one game server."""

from __future__ import annotations

from fastapi import APIRouter

from api.dependencies import ActiveUser, DatabaseSession
from api.routes import scheduled_tasks as legacy
from modules import ScheduledTaskCreate, ScheduledTaskUpdate

from .schemas import (
    ActionResult,
    ScheduledTaskCreateRequest,
    ScheduledTaskUpdateRequest,
    ScheduledTaskView,
)

router = APIRouter(prefix="/api/v1/servers", tags=["v1-schedule"])


def _view(task) -> ScheduledTaskView:
    return ScheduledTaskView(
        id=int(task.id),
        server_id=int(task.server_id),
        name=str(task.name),
        action=str(task.action),
        enabled=bool(task.enabled),
        schedule_type=str(task.schedule_type),
        schedule_value=str(task.schedule_value),
        last_run=task.last_run,
        next_run=task.next_run,
        run_count=int(getattr(task, "run_count", 0) or 0),
        last_status=getattr(task, "last_status", None),
        last_error=getattr(task, "last_error", None),
        created_at=getattr(task, "created_at", None),
        updated_at=getattr(task, "updated_at", None),
    )


@router.get("/{server_id}/schedule", response_model=list[ScheduledTaskView])
async def list_scheduled_tasks(
    server_id: int, db: DatabaseSession, current_user: ActiveUser
) -> list[ScheduledTaskView]:
    tasks = await legacy.list_scheduled_tasks(server_id, db, current_user)
    return [_view(task) for task in tasks]


@router.post("/{server_id}/schedule", response_model=ScheduledTaskView)
async def create_scheduled_task(
    server_id: int,
    body: ScheduledTaskCreateRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ScheduledTaskView:
    task = await legacy.create_scheduled_task(
        server_id,
        ScheduledTaskCreate(
            name=body.name,
            action=body.action,
            enabled=body.enabled,
            schedule_type=body.schedule_type,
            schedule_value=body.schedule_value,
        ),
        db,
        current_user,
    )
    return _view(task)


@router.get("/{server_id}/schedule/{task_id}", response_model=ScheduledTaskView)
async def get_scheduled_task(
    server_id: int,
    task_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ScheduledTaskView:
    return _view(await legacy.get_scheduled_task(server_id, task_id, db, current_user))


@router.put("/{server_id}/schedule/{task_id}", response_model=ScheduledTaskView)
async def update_scheduled_task(
    server_id: int,
    task_id: int,
    body: ScheduledTaskUpdateRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ScheduledTaskView:
    task = await legacy.update_scheduled_task(
        server_id,
        task_id,
        ScheduledTaskUpdate(**body.model_dump(exclude_unset=True)),
        db,
        current_user,
    )
    return _view(task)


@router.delete("/{server_id}/schedule/{task_id}", response_model=ActionResult)
async def delete_scheduled_task(
    server_id: int,
    task_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ActionResult:
    result = await legacy.delete_scheduled_task(server_id, task_id, db, current_user)
    return ActionResult(
        success=bool(result.get("success", True)),
        message=str(result.get("message", "Scheduled task deleted")),
    )


@router.post("/{server_id}/schedule/{task_id}/toggle", response_model=ScheduledTaskView)
async def toggle_scheduled_task(
    server_id: int,
    task_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ScheduledTaskView:
    return _view(await legacy.toggle_scheduled_task(server_id, task_id, db, current_user))
