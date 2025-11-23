"""
API routes for scheduled tasks
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import List

from modules import (
    get_db, get_current_user, User,
    ScheduledTask, Server,
    ScheduledTaskCreate, ScheduledTaskUpdate, ScheduledTaskResponse
)
from services.scheduled_task_service import scheduled_task_service

router = APIRouter(prefix="/api/scheduled-tasks", tags=["scheduled-tasks"])


@router.post("/{server_id}", response_model=ScheduledTaskResponse)
async def create_scheduled_task(
    server_id: int,
    task_data: ScheduledTaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a new scheduled task for a server"""
    # Verify server exists and belongs to user
    result = await db.execute(
        select(Server).filter(Server.id == server_id, Server.user_id == current_user.id)
    )
    server = result.scalar_one_or_none()
    
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    # Create task
    task = ScheduledTask(
        server_id=server_id,
        name=task_data.name,
        action=task_data.action,
        enabled=task_data.enabled,
        schedule_type=task_data.schedule_type,
        schedule_value=task_data.schedule_value
    )
    
    db.add(task)
    await db.commit()
    await db.refresh(task)
    
    # Calculate next run time
    await scheduled_task_service.recalculate_next_run(task.id)
    
    # Refresh to get updated next_run
    await db.refresh(task)
    
    return task


@router.get("/{server_id}", response_model=List[ScheduledTaskResponse])
async def list_scheduled_tasks(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List all scheduled tasks for a server"""
    # Verify server exists and belongs to user
    result = await db.execute(
        select(Server).filter(Server.id == server_id, Server.user_id == current_user.id)
    )
    server = result.scalar_one_or_none()
    
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    # Get tasks
    result = await db.execute(
        select(ScheduledTask)
        .filter(ScheduledTask.server_id == server_id)
        .order_by(ScheduledTask.id.desc())
    )
    tasks = result.scalars().all()
    
    return tasks


@router.get("/{server_id}/tasks/{task_id}", response_model=ScheduledTaskResponse)
async def get_scheduled_task(
    server_id: int,
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get a specific scheduled task"""
    # Verify server exists and belongs to user
    result = await db.execute(
        select(Server).filter(Server.id == server_id, Server.user_id == current_user.id)
    )
    server = result.scalar_one_or_none()
    
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    # Get task
    result = await db.execute(
        select(ScheduledTask).filter(
            ScheduledTask.id == task_id,
            ScheduledTask.server_id == server_id
        )
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    
    return task


@router.put("/{server_id}/tasks/{task_id}", response_model=ScheduledTaskResponse)
async def update_scheduled_task(
    server_id: int,
    task_id: int,
    task_data: ScheduledTaskUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update a scheduled task"""
    # Verify server exists and belongs to user
    result = await db.execute(
        select(Server).filter(Server.id == server_id, Server.user_id == current_user.id)
    )
    server = result.scalar_one_or_none()
    
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    # Get task
    result = await db.execute(
        select(ScheduledTask).filter(
            ScheduledTask.id == task_id,
            ScheduledTask.server_id == server_id
        )
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    
    # Update task fields
    update_data = task_data.model_dump(exclude_unset=True)
    
    # If schedule is being changed, recalculate first before committing
    schedule_changed = 'schedule_type' in update_data or 'schedule_value' in update_data
    if schedule_changed:
        # Apply updates to task object temporarily
        for field, value in update_data.items():
            setattr(task, field, value)
        
        # Try to calculate next run with new values
        try:
            next_run = scheduled_task_service._calculate_next_run(task)
            if next_run is None and task.enabled:
                # If calculation fails for an enabled task, reject the update
                raise HTTPException(
                    status_code=400, 
                    detail="Invalid schedule configuration: cannot calculate next run time"
                )
            task.next_run = next_run
        except Exception as e:
            # Rollback changes
            await db.rollback()
            raise HTTPException(
                status_code=400,
                detail=f"Failed to calculate next run time: {str(e)}"
            )
    else:
        # Just apply the updates
        for field, value in update_data.items():
            setattr(task, field, value)
    
    await db.commit()
    await db.refresh(task)
    
    return task


@router.delete("/{server_id}/tasks/{task_id}")
async def delete_scheduled_task(
    server_id: int,
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete a scheduled task"""
    # Verify server exists and belongs to user
    result = await db.execute(
        select(Server).filter(Server.id == server_id, Server.user_id == current_user.id)
    )
    server = result.scalar_one_or_none()
    
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    # Delete task
    result = await db.execute(
        delete(ScheduledTask).filter(
            ScheduledTask.id == task_id,
            ScheduledTask.server_id == server_id
        )
    )
    
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    
    await db.commit()
    
    return {"success": True, "message": "Scheduled task deleted"}


@router.post("/{server_id}/tasks/{task_id}/toggle", response_model=ScheduledTaskResponse)
async def toggle_scheduled_task(
    server_id: int,
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Toggle a scheduled task enabled/disabled"""
    # Verify server exists and belongs to user
    result = await db.execute(
        select(Server).filter(Server.id == server_id, Server.user_id == current_user.id)
    )
    server = result.scalar_one_or_none()
    
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    # Get task
    result = await db.execute(
        select(ScheduledTask).filter(
            ScheduledTask.id == task_id,
            ScheduledTask.server_id == server_id
        )
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    
    # Toggle enabled
    task.enabled = not task.enabled
    
    await db.commit()
    await db.refresh(task)
    
    # Recalculate next run if enabled
    if task.enabled:
        await scheduled_task_service.recalculate_next_run(task.id)
        await db.refresh(task)
    
    return task
