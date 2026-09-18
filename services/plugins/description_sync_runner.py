"""Serial README refresh execution for one persisted description sync job."""

from __future__ import annotations

import asyncio
import logging
import time

from services.audit_log_service import record_audit_event
from services.github_service import parse_github_url
from services.plugins import description_sync_store as store
from services.plugins.github_ai_client import (
    GitHubAIClient,
    GitHubAuthenticationError,
    GitHubImportError,
    GitHubRateLimitError,
)
from services.plugins.github_readme import decode_readme

logger = logging.getLogger(__name__)

DESCRIPTION_SYNC_INTERVAL_SECONDS = 2.0
DEFAULT_RATE_LIMIT_RETRY_SECONDS = 60


async def _fetch_readme(client: GitHubAIClient, github_url: str) -> str | None:
    try:
        owner, repository = parse_github_url(github_url)
    except ValueError as exc:
        raise GitHubImportError(str(exc)) from exc
    data = await client.request(f"/repos/{owner}/{repository}/readme")
    if not isinstance(data, dict):
        raise GitHubImportError("GitHub returned an invalid README payload")
    return decode_readme(str(data.get("content") or ""))


def _terminal_status(job: store.DescriptionSyncJobSnapshot) -> str:
    if job.status == "completed":
        return "success" if job.failed == 0 else "partial"
    if job.status == "cancelled":
        return "cancelled"
    return "failure"


async def _audit_terminal(job: store.DescriptionSyncJobSnapshot | None) -> None:
    if job is None:
        return
    await record_audit_event(
        category="plugin",
        action="plugin.catalog.sync_descriptions",
        status=_terminal_status(job),
        actor_user_id=job.actor_user_id,
        details={
            "operation_id": job.operation_id,
            "framework": job.framework,
            "overwrite": job.overwrite,
            "total": job.total,
            "processed": job.processed,
            "updated": job.updated,
            "unchanged": job.unchanged,
            "skipped": job.skipped,
            "failed": job.failed,
            "stop_reason": job.stop_reason,
        },
    )


async def _finish(
    job_id: str,
    *,
    status: str,
    phase: str,
    message: str,
    reason: str | None = None,
) -> None:
    job = await store.finish_job(
        job_id,
        status=status,
        phase=phase,
        message=message,
        reason=reason,
    )
    await _audit_terminal(job)


async def _handle_cancelled(job_id: str) -> None:
    cancelled = await store.is_cancel_requested(job_id)
    if cancelled:
        await _finish(
            job_id,
            status="cancelled",
            phase="cancelled",
            message="Description sync cancelled; completed items were retained",
            reason="cancelled",
        )
        return
    await store.requeue_job(job_id, "Description sync interrupted; will resume")


async def run_job(job: store.DescriptionSyncJobSnapshot) -> None:
    """Run one claimed job, persisting after every listing."""
    try:
        github_token = await store.credentials(job.actor_user_id)
        client = GitHubAIClient(
            github_token,
            require_token=False,
            interval=DESCRIPTION_SYNC_INTERVAL_SECONDS,
        )
    except PermissionError:
        await _finish(
            job.operation_id,
            status="failed",
            phase="failed",
            message="Administrator access changed; description sync stopped",
            reason="configuration",
        )
        return

    try:
        for index in range(job.processed, job.total):
            if await store.is_cancel_requested(job.operation_id):
                await _finish(
                    job.operation_id,
                    status="cancelled",
                    phase="cancelled",
                    message="Description sync cancelled; completed items were retained",
                    reason="cancelled",
                )
                return

            plugin_id = job.target_ids[index]
            target = await store.load_target(plugin_id)
            if target is None:
                await store.record_item(
                    job.operation_id,
                    plugin_id=plugin_id,
                    title=f"Plugin #{plugin_id}",
                    github_url="",
                    action="skipped",
                    message="Marketplace listing no longer exists",
                )
                continue
            if not job.overwrite and (target.description or "").strip():
                await store.record_item(
                    job.operation_id,
                    plugin_id=target.plugin_id,
                    title=target.title,
                    github_url=target.github_url,
                    action="skipped",
                    message="Description already set",
                )
                continue

            await store.set_current(job.operation_id, target)
            try:
                readme = await _fetch_readme(client, target.github_url)
            except GitHubRateLimitError as exc:
                retry_at = exc.reset_at or int(time.time() + DEFAULT_RATE_LIMIT_RETRY_SECONDS)
                await store.mark_waiting(
                    job.operation_id,
                    retry_at=retry_at,
                    message="GitHub rate limit reached; the task will resume automatically",
                )
                return
            except GitHubAuthenticationError:
                await _finish(
                    job.operation_id,
                    status="failed",
                    phase="failed",
                    message="GitHub token is invalid or expired; check Settings",
                    reason="configuration",
                )
                return
            except (GitHubImportError, ValueError) as exc:
                await store.record_item(
                    job.operation_id,
                    plugin_id=target.plugin_id,
                    title=target.title,
                    github_url=target.github_url,
                    action="failed",
                    message=str(exc)[:2000],
                )
                continue

            if not readme:
                await store.record_item(
                    job.operation_id,
                    plugin_id=target.plugin_id,
                    title=target.title,
                    github_url=target.github_url,
                    action="skipped",
                    message="Repository has no README",
                )
                continue
            if (target.description or "") == readme:
                await store.record_item(
                    job.operation_id,
                    plugin_id=target.plugin_id,
                    title=target.title,
                    github_url=target.github_url,
                    action="unchanged",
                )
                continue
            await store.record_item(
                job.operation_id,
                plugin_id=target.plugin_id,
                title=target.title,
                github_url=target.github_url,
                action="updated",
                description=readme,
            )

        await _finish(
            job.operation_id,
            status="completed",
            phase="completed",
            message="Description sync completed",
        )
    except asyncio.CancelledError:
        try:
            await asyncio.shield(_handle_cancelled(job.operation_id))
        except Exception:
            logger.exception("Failed to persist interrupted description sync %s", job.operation_id)
        raise
    except PermissionError:
        await _finish(
            job.operation_id,
            status="failed",
            phase="failed",
            message="Credentials or administrator access changed; check Settings",
            reason="configuration",
        )
    except Exception:
        logger.exception("Description sync job %s failed", job.operation_id)
        await _finish(
            job.operation_id,
            status="failed",
            phase="failed",
            message="Description sync failed; completed items were retained",
            reason="execution_error",
        )
    finally:
        await client.close()
