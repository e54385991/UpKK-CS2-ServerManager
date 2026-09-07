"""Short transactions for marketplace import jobs and token verification."""

from __future__ import annotations

import asyncio
import hashlib
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from modules.database import async_session_maker
from modules.models import (
    MarketPlugin,
    PluginCategory,
    PluginFramework,
    PluginImportJob,
    SystemSettings,
    User,
)
from modules.plugin_ai import (
    GitHubVerification,
    ImportEvent,
    ImportItem,
    ImportOptions,
    PluginAIInfo,
    RepositoryAnalysis,
    repository_url,
)
from services.ai_security import AIProviderConfig, get_effective_provider
from services.plugins.github_ai_client import GitHubAIClient, GitHubImportError
from services.redis_manager import redis_manager

ACTIVE = ("queued", "running")
WORKER_LOCK = "plugin_import:worker"
worker_lease: ContextVar[str | None] = ContextVar("plugin_import_worker_lease", default=None)


async def check_lease() -> None:
    lease = worker_lease.get()
    if lease is not None and not await redis_manager.refresh_lock(WORKER_LOCK, lease, expire=45):
        raise asyncio.CancelledError


async def check_administrator(actor_id: int) -> None:
    async with async_session_maker() as db:
        await authorize(db, actor_id)


def now() -> datetime:
    return datetime.now(timezone.utc)


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.strip().encode()).hexdigest()


async def authorize(db: AsyncSession, actor_id: int) -> User:
    user = await db.get(User, actor_id)
    if user is None or not user.is_active or not user.is_admin:
        raise PermissionError("Administrator access is no longer available")
    return user


def verification_for(settings: SystemSettings) -> GitHubVerification:
    token = settings.global_github_token or ""
    if not token or settings.github_token_fingerprint != fingerprint(token):
        return GitHubVerification(message="Save and verify the global GitHub token in Settings")
    return GitHubVerification.model_validate(settings.github_token_verification or {})


async def verify_token(actor_id: int) -> GitHubVerification:
    async with async_session_maker() as db:
        await authorize(db, actor_id)
        settings = await SystemSettings.get_or_create_settings(db)
        token = (settings.global_github_token or "").strip()
    if not token:
        return GitHubVerification(message="Save the global GitHub token first")
    client = GitHubAIClient(token)
    try:
        result = await client.verify()
    except GitHubImportError as exc:
        result = GitHubVerification(message=str(exc), checked_at=now().isoformat())
    finally:
        await client.close()
    async with async_session_maker() as db:
        await authorize(db, actor_id)
        settings = await SystemSettings.get_or_create_settings(db)
        if fingerprint(settings.global_github_token or "") != fingerprint(token):
            return GitHubVerification(
                message="GitHub token changed during validation; verify again"
            )
        settings.github_token_fingerprint = fingerprint(token)
        settings.github_token_verification = result.model_dump(mode="json")
        db.add(settings)
        await db.commit()
    return result


async def readiness(actor_id: int) -> tuple[GitHubVerification, AIProviderConfig | None]:
    async with async_session_maker() as db:
        user = await authorize(db, actor_id)
        settings = await SystemSettings.get_or_create_settings(db)
        verification = verification_for(settings)
        config = await get_effective_provider(db, user, require_tested=True)
    return verification, config


async def credentials(actor_id: int) -> tuple[str, AIProviderConfig]:
    async with async_session_maker() as db:
        user = await authorize(db, actor_id)
        settings = await SystemSettings.get_or_create_settings(db)
        if not verification_for(settings).valid:
            raise PermissionError("Verify the saved global GitHub token in Settings first")
        token = str(settings.global_github_token).strip()
        config = await get_effective_provider(db, user, require_tested=True)
        if config is None:
            raise PermissionError(
                "Configure and test the current administrator's AI provider first"
            )
    return token, config


@dataclass(frozen=True)
class JobSnapshot:
    operation_id: str
    actor_user_id: int
    status: str
    command: str
    options: ImportOptions
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    phase: str
    message: str
    current_repository: str | None
    model: str | None
    stop_reason: str | None
    retry_at: int | None
    cancel_requested: bool
    items: list[ImportItem]
    events: list[ImportEvent]


def snapshot(job: PluginImportJob) -> JobSnapshot:
    return JobSnapshot(
        operation_id=job.id,
        actor_user_id=job.actor_user_id,
        status=job.status,
        command=job.command,
        options=ImportOptions.model_validate(job.options),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        phase=job.phase,
        message=job.message,
        current_repository=job.current_repository,
        model=job.model,
        stop_reason=job.stop_reason,
        retry_at=job.retry_at,
        cancel_requested=job.cancel_requested,
        items=[ImportItem.model_validate(item) for item in job.items],
        events=[ImportEvent.model_validate(event) for event in job.events],
    )


def append_event(
    job: PluginImportJob, phase: str, message: str, repository: str | None = None
) -> None:
    sequence = int(str(job.events[-1]["sequence"])) + 1 if job.events else 1
    event = ImportEvent(
        sequence=sequence, phase=phase, message=message[:2000], repository=repository
    )
    job.events = [*job.events, event.model_dump(mode="json")][-300:]
    job.phase, job.message, job.current_repository = phase, message[:2000], repository


async def enqueue(actor_id: int, options: ImportOptions, request_id: UUID) -> JobSnapshot:
    await credentials(actor_id)
    lease = str(uuid4())
    acquired = await redis_manager.acquire_lock("plugin_import:enqueue", lease, expire=10)
    if not acquired:
        raise RuntimeError("Import queue is temporarily unavailable")
    try:
        async with async_session_maker() as db:
            await authorize(db, actor_id)
            key = f"{actor_id}:{request_id}"
            existing = (
                (
                    await db.execute(
                        select(PluginImportJob).where(PluginImportJob.request_key == key)
                    )
                )
                .scalars()
                .first()
            )
            if existing:
                return snapshot(existing)
            pending = await db.scalar(
                select(func.count())
                .select_from(PluginImportJob)
                .where(PluginImportJob.status == "queued")
            )
            if int(pending or 0) >= 10:
                raise ValueError("Import queue is full (10 pending jobs)")
            command = f"AI import: {options.framework}; sort={options.sort}; stars>={options.min_stars}; forks>={options.min_forks}; active={options.updated_within_days}d; {options.minutes}min; max={options.max_plugins}; {options.keywords}"
            job = PluginImportJob(
                actor_user_id=actor_id,
                request_key=key,
                options=options.model_dump(mode="json"),
                command=command,
                created_at=now(),
                heartbeat_at=now(),
            )
            append_event(job, "queued", "Queued for AI marketplace import")
            db.add(job)
            await db.commit()
            await db.refresh(job)
            return snapshot(job)
    finally:
        await redis_manager.release_lock("plugin_import:enqueue", lease)


async def get_job(job_id: str) -> JobSnapshot | None:
    async with async_session_maker() as db:
        job = await db.get(PluginImportJob, job_id)
        return snapshot(job) if job else None


async def list_jobs(*, active_only: bool = False) -> list[JobSnapshot]:
    async with async_session_maker() as db:
        query = select(PluginImportJob).where(
            PluginImportJob.created_at >= now() - timedelta(days=7)
        )
        if active_only:
            query = query.where(col(PluginImportJob.status).in_((*ACTIVE, "failed")))
        jobs = (
            await db.execute(query.order_by(col(PluginImportJob.created_at).desc()).limit(100))
        ).scalars()
        return [snapshot(job) for job in jobs]


async def clear_failed_jobs(actor_id: int) -> int:
    """Drop retained failed import jobs so the tray can be emptied in one click.

    Failed jobs stay listed for seven days next to the failed server
    operations. Without this they were the one kind of failure an operator
    could not dismiss, so the tray kept a permanent red badge.
    """
    async with async_session_maker() as db:
        await authorize(db, actor_id)
        jobs = (
            await db.execute(select(PluginImportJob).where(PluginImportJob.status == "failed"))
        ).scalars()
        cleared = 0
        for job in jobs:
            await db.delete(job)
            cleared += 1
        await db.commit()
        return cleared


async def cancel_job(job_id: str, actor_id: int) -> JobSnapshot:
    async with async_session_maker() as db:
        await authorize(db, actor_id)
        job = (
            (
                await db.execute(
                    select(PluginImportJob).where(PluginImportJob.id == job_id).with_for_update()
                )
            )
            .scalars()
            .first()
        )
        if not job:
            raise LookupError("Import task not found")
        if job.status in ACTIVE:
            job.cancel_requested = True
            if job.status == "queued":
                job.status, job.stop_reason, job.completed_at = "cancelled", "cancelled", now()
                append_event(job, "cancelled", "Cancelled before execution")
            db.add(job)
            await db.commit()
            await db.refresh(job)
        return snapshot(job)


async def update_job(
    job_id: str,
    *,
    phase: str,
    message: str,
    repository: str | None = None,
    status: str | None = None,
    reason: str | None = None,
    retry_at: int | None = None,
    item: ImportItem | None = None,
    model: str | None = None,
) -> None:
    async with async_session_maker() as db:
        job = (
            (
                await db.execute(
                    select(PluginImportJob).where(PluginImportJob.id == job_id).with_for_update()
                )
            )
            .scalars()
            .first()
        )
        if job is None or job.status not in ACTIVE:
            return
        append_event(job, phase, message, repository)
        job.heartbeat_at = now()
        if item:
            job.items = [*job.items, item.model_dump(mode="json")]
        if model:
            job.model = model
        if status:
            job.status = status
            if status == "running":
                job.started_at = now()
            else:
                job.completed_at, job.stop_reason, job.retry_at = now(), reason, retry_at
        db.add(job)
        await db.commit()


async def check_job(job_id: str, token_fingerprint: str) -> None:
    await check_lease()
    async with async_session_maker() as db:
        job = await db.get(PluginImportJob, job_id)
        if job is None or job.status not in ACTIVE or job.cancel_requested:
            raise asyncio.CancelledError
        await authorize(db, job.actor_user_id)
        settings = await SystemSettings.get_or_create_settings(db)
        if fingerprint(settings.global_github_token or "") != token_fingerprint:
            raise PermissionError("Global GitHub token changed; submit a new task")


async def existing_plugin(url: str) -> int | None:
    async with async_session_maker() as db:
        rows = (await db.execute(select(MarketPlugin.id, MarketPlugin.github_url))).all()
        for row in rows:
            try:
                if repository_url(str(row[1])) == url.casefold():
                    return int(row[0])
            except ValueError:
                continue
    return None


async def insert_plugin(
    job_id: str,
    url: str,
    author: str,
    analysis: RepositoryAnalysis,
    metadata: PluginAIInfo,
    dependency_ids: list[int],
    token_fingerprint: str,
) -> int:
    await check_lease()
    async with async_session_maker() as db:
        job = (
            (
                await db.execute(
                    select(PluginImportJob).where(PluginImportJob.id == job_id).with_for_update()
                )
            )
            .scalars()
            .first()
        )
        if job is None or job.status != "running" or job.cancel_requested:
            raise asyncio.CancelledError
        await authorize(db, job.actor_user_id)
        settings = await SystemSettings.get_or_create_settings(db)
        if fingerprint(settings.global_github_token or "") != token_fingerprint:
            raise PermissionError("Global GitHub token changed")
        existing = (
            (
                await db.execute(
                    select(MarketPlugin).where(func.lower(MarketPlugin.github_url) == url)
                )
            )
            .scalars()
            .first()
        )
        if existing:
            return int(existing.id)
        plugin = MarketPlugin(
            github_url=url,
            title=analysis.title,
            description=analysis.description,
            author=author,
            category=PluginCategory(analysis.category),
            framework=PluginFramework(analysis.framework),
            dependencies=",".join(map(str, dependency_ids)) or None,
            custom_install_path=metadata.installation.target_path
            if metadata.installation
            else None,
            ai_metadata=metadata.model_dump(mode="json"),
        )
        db.add(plugin)
        await db.flush()
        item = ImportItem(
            repository=url,
            status="imported",
            plugin_id=int(plugin.id),
            message="AI generated; review before installation",
        )
        job.items = [*job.items, item.model_dump(mode="json")]
        append_event(job, "importing", item.message, url)
        db.add(job)
        await db.commit()
        return int(plugin.id)


async def claim_next() -> JobSnapshot | None:
    async with async_session_maker() as db:
        await db.execute(
            delete(PluginImportJob).where(
                col(PluginImportJob.created_at) < now() - timedelta(days=7),
                col(PluginImportJob.status).not_in(ACTIVE),
            )
        )
        job = (
            (
                await db.execute(
                    select(PluginImportJob)
                    .where(PluginImportJob.status == "queued")
                    .order_by(col(PluginImportJob.created_at))
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .first()
        )
        if job:
            job.status, job.started_at, job.heartbeat_at = "running", now(), now()
            append_event(job, "starting", "Validating credentials")
            db.add(job)
        await db.commit()
        return snapshot(job) if job else None


async def reconcile_orphans(boot_time: datetime) -> None:
    async with async_session_maker() as db:
        jobs = (
            await db.execute(
                select(PluginImportJob)
                .where(col(PluginImportJob.status).in_(ACTIVE))
                .with_for_update()
            )
        ).scalars()
        for job in jobs:
            if job.status == "running" or job.created_at < boot_time:
                job.status, job.stop_reason, job.completed_at = "failed", "interrupted", now()
                append_event(
                    job,
                    "failed",
                    "Worker stopped; submit again to continue with remaining repositories",
                )
                db.add(job)
        await db.commit()


async def review_plugin(plugin_id: int, actor_id: int, metadata: PluginAIInfo) -> PluginAIInfo:
    async with async_session_maker() as db:
        await authorize(db, actor_id)
        plugin = await db.get(MarketPlugin, plugin_id)
        if plugin is None or plugin.ai_metadata is None:
            raise LookupError("AI-generated plugin not found")
        original = PluginAIInfo.model_validate(plugin.ai_metadata)
        metadata.model = original.model
        metadata.sources = original.sources
        plugin.ai_metadata = metadata.model_dump(mode="json")
        plugin.custom_install_path = (
            metadata.installation.target_path if metadata.installation else None
        )
        db.add(plugin)
        await db.commit()
        return metadata
