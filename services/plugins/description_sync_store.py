"""Durable store for throttled marketplace description sync jobs."""

from __future__ import annotations

import hashlib
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, func, or_
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col, select

from modules.database import async_session_maker
from modules.models import (
    MarketPlugin,
    PluginDescriptionSyncJob,
    PluginFramework,
    User,
)
from modules.observability import record_error, record_task
from services.github_credentials import get_effective_github_token
from services.plugins.types import DescriptionSyncAction, DescriptionSyncItem
from services.redis_manager import redis_manager

ACTIVE = ("queued", "running", "waiting")
TERMINAL_VISIBLE = ("completed", "cancelled")
DescriptionSyncStatus = Literal["queued", "running", "waiting", "completed", "failed", "cancelled"]
FAILED_RETENTION = timedelta(days=7)
COMPLETED_RETENTION = timedelta(hours=24)
MAX_PENDING_JOBS = 10
MAX_STORED_ITEMS = 500
WORKER_LOCK = "plugin_description_sync:worker"
ENQUEUE_LOCK = "plugin_description_sync:enqueue"
worker_lease: ContextVar[str | None] = ContextVar(
    "plugin_description_sync_worker_lease", default=None
)


def now() -> datetime:
    return datetime.now(timezone.utc)


def _observe_status(status: str, message: str, job_id: str) -> None:
    if status == "completed":
        record_task("succeeded")
        return
    if status == "cancelled":
        record_task("cancelled")
        return
    if status != "failed":
        return
    record_task("failed")
    record_error(
        source="task",
        summary=message,
        error_code="plugin_description_sync_failed",
        operation_id=job_id,
    )


@dataclass(frozen=True)
class TargetPlugin:
    plugin_id: int
    title: str
    github_url: str
    description: str | None


@dataclass(frozen=True)
class DescriptionSyncJobSnapshot:
    operation_id: str
    actor_user_id: int
    status: DescriptionSyncStatus
    command: str
    framework: str | None
    overwrite: bool
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    phase: str
    message: str
    current_plugin_id: int | None
    current_plugin_title: str | None
    current_github_url: str | None
    stop_reason: str | None
    retry_at: int | None
    cancel_requested: bool
    target_ids: list[int]
    total: int
    processed: int
    updated: int
    unchanged: int
    skipped: int
    failed: int
    items: list[DescriptionSyncItem] = field(default_factory=list)


def snapshot(job: PluginDescriptionSyncJob) -> DescriptionSyncJobSnapshot:
    return DescriptionSyncJobSnapshot(
        operation_id=job.id,
        actor_user_id=job.actor_user_id,
        status=cast(DescriptionSyncStatus, job.status),
        command=job.command,
        framework=job.framework,
        overwrite=job.overwrite,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        phase=job.phase,
        message=job.message,
        current_plugin_id=job.current_plugin_id,
        current_plugin_title=job.current_plugin_title,
        current_github_url=job.current_github_url,
        stop_reason=job.stop_reason,
        retry_at=job.retry_at,
        cancel_requested=job.cancel_requested,
        target_ids=list(job.target_ids),
        total=job.total,
        processed=job.cursor,
        updated=job.updated,
        unchanged=job.unchanged,
        skipped=job.skipped,
        failed=job.failed,
        items=[
            DescriptionSyncItem(
                plugin_id=cast(int, item["plugin_id"]),
                title=str(item["title"]),
                github_url=str(item["github_url"]),
                action=cast(DescriptionSyncAction, item["action"]),
                message=(str(item["message"]) if item.get("message") is not None else None),
            )
            for item in job.items
        ],
    )


async def authorize(actor_id: int) -> None:
    async with async_session_maker() as db:
        user = await db.get(User, actor_id)
        if user is None or not user.is_active or not user.is_admin:
            raise PermissionError("Administrator access is no longer available")


async def credentials(actor_id: int) -> str | None:
    async with async_session_maker() as db:
        user = await db.get(User, actor_id)
        if user is None or not user.is_active or not user.is_admin:
            raise PermissionError("Administrator access is no longer available")
        return await get_effective_github_token(db, user)


async def _target_ids(
    plugin_ids: list[int] | None,
    framework: PluginFramework | None,
) -> list[int]:
    async with async_session_maker() as db:
        query = select(MarketPlugin.id).order_by(col(MarketPlugin.id))
        if plugin_ids:
            query = query.where(col(MarketPlugin.id).in_(list(dict.fromkeys(plugin_ids))))
        if framework is not None:
            query = query.where(MarketPlugin.framework == framework)
        return [int(value) for value in (await db.execute(query)).scalars().all()]


def _scope_key(
    plugin_ids: list[int] | None,
    framework: PluginFramework | None,
    overwrite: bool,
) -> str:
    target_key = (
        f"plugins:{','.join(str(value) for value in sorted(set(plugin_ids)))}"
        if plugin_ids
        else f"framework:{framework.value if framework else 'all'}"
    )
    digest = hashlib.sha256(target_key.encode()).hexdigest()
    return f"{digest}:{'overwrite' if overwrite else 'fill'}"


def _retention_clause() -> ColumnElement[bool]:
    current = now()
    terminal_time = func.coalesce(
        PluginDescriptionSyncJob.completed_at,
        PluginDescriptionSyncJob.created_at,
    )
    return or_(
        col(PluginDescriptionSyncJob.status).in_(ACTIVE),
        and_(
            col(PluginDescriptionSyncJob.status) == "failed",
            terminal_time >= current - FAILED_RETENTION,
        ),
        and_(
            col(PluginDescriptionSyncJob.status).in_(TERMINAL_VISIBLE),
            terminal_time >= current - COMPLETED_RETENTION,
        ),
    )


async def enqueue(
    actor_id: int,
    request_id: UUID,
    *,
    plugin_ids: list[int] | None = None,
    framework: PluginFramework | None = None,
    overwrite: bool = True,
) -> DescriptionSyncJobSnapshot:
    await authorize(actor_id)
    lease = str(uuid4())
    acquired = await redis_manager.acquire_lock(ENQUEUE_LOCK, lease, expire=10)
    if not acquired:
        raise RuntimeError("Description sync queue is temporarily unavailable")
    try:
        target_ids = await _target_ids(plugin_ids, framework)
        scope_key = _scope_key(plugin_ids, framework, overwrite)
        request_key = f"description_sync:{actor_id}:{request_id}"
        async with async_session_maker() as db:
            existing = (
                (
                    await db.execute(
                        select(PluginDescriptionSyncJob).where(
                            PluginDescriptionSyncJob.request_key == request_key
                        )
                    )
                )
                .scalars()
                .first()
            )
            if existing is not None:
                return snapshot(existing)

            duplicate = (
                (
                    await db.execute(
                        select(PluginDescriptionSyncJob)
                        .where(
                            PluginDescriptionSyncJob.actor_user_id == actor_id,
                            PluginDescriptionSyncJob.scope_key == scope_key,
                            col(PluginDescriptionSyncJob.status).in_(ACTIVE),
                        )
                        .order_by(col(PluginDescriptionSyncJob.created_at))
                    )
                )
                .scalars()
                .first()
            )
            if duplicate is not None:
                return snapshot(duplicate)

            pending = await db.scalar(
                select(func.count())
                .select_from(PluginDescriptionSyncJob)
                .where(col(PluginDescriptionSyncJob.status).in_(("queued", "waiting")))
            )
            if int(pending or 0) >= MAX_PENDING_JOBS:
                raise ValueError(
                    f"Description sync queue is full ({MAX_PENDING_JOBS} pending jobs)"
                )

            framework_value = framework.value if framework else None
            command = (
                "Sync plugin descriptions: "
                f"framework={framework_value or 'all'}; "
                f"plugins={'selected' if plugin_ids else 'all'}; "
                f"overwrite={'on' if overwrite else 'off'}"
            )
            job = PluginDescriptionSyncJob(
                actor_user_id=actor_id,
                request_key=request_key,
                scope_key=scope_key,
                framework=framework_value,
                overwrite=overwrite,
                command=command,
                target_ids=target_ids,
                total=len(target_ids),
                created_at=now(),
                heartbeat_at=now(),
                phase="queued",
                message=f"Queued {len(target_ids)} plugin description(s)",
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)
            record_task("submitted")
            return snapshot(job)
    finally:
        await redis_manager.release_lock(ENQUEUE_LOCK, lease)


async def get_job(job_id: str) -> DescriptionSyncJobSnapshot | None:
    async with async_session_maker() as db:
        job = await db.get(PluginDescriptionSyncJob, job_id)
        return snapshot(job) if job else None


async def list_jobs() -> list[DescriptionSyncJobSnapshot]:
    async with async_session_maker() as db:
        jobs = (
            await db.execute(
                select(PluginDescriptionSyncJob)
                .where(_retention_clause())
                .order_by(col(PluginDescriptionSyncJob.created_at).desc())
                .limit(100)
            )
        ).scalars()
        return [snapshot(job) for job in jobs]


async def reconcile_orphans() -> int:
    """Requeue jobs interrupted by a process restart without losing progress."""
    async with async_session_maker() as db:
        jobs = (
            await db.execute(
                select(PluginDescriptionSyncJob).where(PluginDescriptionSyncJob.status == "running")
            )
        ).scalars()
        reconciled = 0
        for job in jobs:
            if job.cancel_requested:
                job.status = "cancelled"
                job.phase = "cancelled"
                job.message = "Cancelled while the panel was restarting"
                job.completed_at = now()
                job.stop_reason = "cancelled"
            else:
                job.status = "queued"
                job.phase = "queued"
                job.message = "Resuming after panel restart"
            job.heartbeat_at = now()
            db.add(job)
            reconciled += 1
        if reconciled:
            await db.commit()
        return reconciled


async def claim_next() -> DescriptionSyncJobSnapshot | None:
    async with async_session_maker() as db:
        job = (
            (
                await db.execute(
                    select(PluginDescriptionSyncJob)
                    .where(col(PluginDescriptionSyncJob.status).in_(ACTIVE))
                    .order_by(col(PluginDescriptionSyncJob.created_at))
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .first()
        )
        if job is None:
            return None
        if job.cancel_requested:
            job.status = "cancelled"
            job.phase = "cancelled"
            job.message = "Cancelled before execution"
            job.completed_at = now()
            job.stop_reason = "cancelled"
            db.add(job)
            await db.commit()
            _observe_status(job.status, job.message, job.id)
            return snapshot(job)
        current = int(now().timestamp())
        if job.status == "waiting" and job.retry_at is not None and job.retry_at > current:
            await db.commit()
            return snapshot(job)
        job.status = "running"
        job.phase = "starting"
        job.message = "Starting description sync"
        job.retry_at = None
        job.heartbeat_at = now()
        if job.started_at is None:
            job.started_at = now()
        db.add(job)
        await db.commit()
        await db.refresh(job)
        record_task("running")
        return snapshot(job)


async def load_target(plugin_id: int) -> TargetPlugin | None:
    async with async_session_maker() as db:
        plugin = await db.get(MarketPlugin, plugin_id)
        if plugin is None:
            return None
        return TargetPlugin(
            plugin_id=int(plugin.id),
            title=plugin.title,
            github_url=plugin.github_url,
            description=plugin.description,
        )


async def set_current(job_id: str, target: TargetPlugin) -> None:
    async with async_session_maker() as db:
        job = await db.get(PluginDescriptionSyncJob, job_id)
        if job is None or job.status not in ACTIVE:
            return
        job.phase = "fetching"
        job.message = f"Fetching README for {target.title}"
        job.current_plugin_id = target.plugin_id
        job.current_plugin_title = target.title
        job.current_github_url = target.github_url
        job.heartbeat_at = now()
        db.add(job)
        await db.commit()


async def record_item(
    job_id: str,
    *,
    plugin_id: int,
    title: str,
    github_url: str,
    action: DescriptionSyncAction,
    message: str | None = None,
    description: str | None = None,
) -> None:
    async with async_session_maker() as db:
        job = (
            (
                await db.execute(
                    select(PluginDescriptionSyncJob)
                    .where(PluginDescriptionSyncJob.id == job_id)
                    .with_for_update()
                )
            )
            .scalars()
            .first()
        )
        if job is None or job.status not in ACTIVE:
            return
        if job.cursor >= job.total or job.target_ids[job.cursor] != plugin_id:
            return
        if action == "updated" and description is not None:
            plugin = await db.get(MarketPlugin, plugin_id)
            if plugin is not None:
                plugin.description = description
                plugin.description_i18n = None
                db.add(plugin)
        item = DescriptionSyncItem(
            plugin_id=plugin_id,
            title=title,
            github_url=github_url,
            action=action,
            message=message,
        )
        job.items = [*job.items, asdict(item)][-MAX_STORED_ITEMS:]
        job.cursor += 1
        setattr(job, action, int(getattr(job, action)) + 1)
        job.current_plugin_id = None
        job.current_plugin_title = None
        job.current_github_url = None
        job.phase = "running"
        job.message = f"Processed {job.cursor}/{job.total}"
        job.heartbeat_at = now()
        db.add(job)
        await db.commit()


async def mark_waiting(job_id: str, *, retry_at: int, message: str) -> None:
    async with async_session_maker() as db:
        job = await db.get(PluginDescriptionSyncJob, job_id)
        if job is None or job.status not in ACTIVE:
            return
        job.status = "waiting"
        job.phase = "rate_limited"
        job.message = message[:2000]
        job.retry_at = retry_at
        job.heartbeat_at = now()
        db.add(job)
        await db.commit()


async def heartbeat(job_id: str) -> None:
    async with async_session_maker() as db:
        job = await db.get(PluginDescriptionSyncJob, job_id)
        if job is None or job.status not in ACTIVE:
            return
        job.heartbeat_at = now()
        db.add(job)
        await db.commit()


async def requeue_job(job_id: str, message: str = "Interrupted; will resume") -> None:
    """Release a running job for a later worker without advancing its cursor."""
    async with async_session_maker() as db:
        job = await db.get(PluginDescriptionSyncJob, job_id)
        if job is None or job.status not in ACTIVE:
            return
        job.status = "queued"
        job.phase = "queued"
        job.message = message[:2000]
        job.heartbeat_at = now()
        db.add(job)
        await db.commit()


async def is_cancel_requested(job_id: str) -> bool:
    async with async_session_maker() as db:
        job = await db.get(PluginDescriptionSyncJob, job_id)
        return bool(job and job.cancel_requested)


async def finish_job(
    job_id: str,
    *,
    status: str,
    phase: str,
    message: str,
    reason: str | None = None,
) -> DescriptionSyncJobSnapshot | None:
    async with async_session_maker() as db:
        job = await db.get(PluginDescriptionSyncJob, job_id)
        if job is None:
            return None
        if job.status not in ACTIVE:
            return snapshot(job)
        job.status = status
        job.phase = phase
        job.message = message[:2000]
        job.stop_reason = reason
        job.retry_at = None
        job.completed_at = now()
        job.heartbeat_at = now()
        db.add(job)
        await db.commit()
        await db.refresh(job)
        _observe_status(status, message, job_id)
        return snapshot(job)


async def cancel_job(job_id: str, actor_id: int) -> DescriptionSyncJobSnapshot:
    await authorize(actor_id)
    async with async_session_maker() as db:
        job = (
            (
                await db.execute(
                    select(PluginDescriptionSyncJob)
                    .where(PluginDescriptionSyncJob.id == job_id)
                    .with_for_update()
                )
            )
            .scalars()
            .first()
        )
        if job is None:
            raise LookupError("Description sync task not found")
        if job.status in ACTIVE:
            job.cancel_requested = True
            if job.status != "running":
                job.status = "cancelled"
                job.phase = "cancelled"
                job.message = "Cancelled before execution"
                job.completed_at = now()
                job.stop_reason = "cancelled"
                _observe_status(job.status, job.message, job.id)
            db.add(job)
            await db.commit()
            await db.refresh(job)
        return snapshot(job)


async def delete_job(job_id: str, actor_id: int) -> None:
    await authorize(actor_id)
    async with async_session_maker() as db:
        job = await db.get(PluginDescriptionSyncJob, job_id)
        if job is None:
            raise LookupError("Description sync task not found")
        if job.status in ACTIVE:
            raise ValueError("Cancel the description sync before deleting it")
        await db.delete(job)
        await db.commit()


async def clear_failed_jobs(actor_id: int) -> int:
    await authorize(actor_id)
    async with async_session_maker() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(PluginDescriptionSyncJob)
            .where(col(PluginDescriptionSyncJob.status) == "failed")
        )
        await db.execute(
            delete(PluginDescriptionSyncJob).where(col(PluginDescriptionSyncJob.status) == "failed")
        )
        await db.commit()
        return int(count or 0)
