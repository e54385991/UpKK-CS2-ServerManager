"""Serial GitHub release synchronization and invalid-listing detection runner."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from modules.models import MarketPlugin
from services.audit_log_service import record_audit_event
from services.github_service import parse_github_url
from services.plugins import release_sync_store as store
from services.plugins.ai_install_policy import metadata, validate_installable
from services.plugins.common import PluginPlanError
from services.plugins.github_ai_client import (
    GitHubAIClient,
    GitHubAuthenticationError,
    GitHubImportError,
    GitHubRateLimitError,
)
from services.plugins.github_assets import GitHubPlanError
from services.plugins.market_release_sync import _prepare_snapshot
from services.plugins.panel_frameworks import panel_framework_key
from services.plugins.release_selection import (
    MarketReleaseSync,
    market_release_sync,
    match_archives,
    release_assets,
    release_family,
    suggest_asset_glob,
    verified_snapshot_assets,
)
from services.plugins.tracking import canonical_repo_url
from services.plugins.types import ReleaseIssueCode, ReleaseSyncItem, ReleaseSyncMode

logger = logging.getLogger(__name__)

RELEASE_SYNC_INTERVAL_SECONDS = 2.0
DEFAULT_RATE_LIMIT_RETRY_SECONDS = 60


@dataclass(frozen=True)
class EvaluatedListing:
    item: ReleaseSyncItem
    release_sync: dict[str, object] | None = None


@dataclass(frozen=True)
class SelectedReleasePlan:
    pattern: str | None = None
    selected: list[dict[str, Any]] | None = None
    issue_code: ReleaseIssueCode | None = None
    reason: str | None = None


def _is_transient_download_error(message: str) -> bool:
    lowered = message.casefold()
    return any(
        marker in lowered
        for marker in ("http 429", "http 500", "http 502", "http 503", "http 504", "timed out")
    )


async def _classify_missing_release(
    client: GitHubAIClient, owner: str, repository: str
) -> tuple[ReleaseIssueCode, str]:
    try:
        repo_data = await client.optional(f"/repos/{owner}/{repository}")
    except GitHubImportError as exc:
        if exc.status in (410, 451):
            return "repository_missing", "GitHub repository is unavailable"
        return "upstream_error", str(exc)
    if repo_data is None or (isinstance(repo_data, dict) and repo_data.get("private")):
        return "repository_missing", "GitHub repository not found or is private"
    return "release_missing", "Repository has no stable GitHub release"


async def _fetch_latest_release(
    client: GitHubAIClient, github_url: str
) -> tuple[dict[str, Any] | None, ReleaseIssueCode | None, str | None]:
    try:
        owner, repository = parse_github_url(github_url)
    except ValueError as exc:
        return None, "invalid_url", str(exc)
    try:
        data = await client.request(f"/repos/{owner}/{repository}/releases/latest")
    except GitHubRateLimitError, GitHubAuthenticationError:
        raise
    except GitHubImportError as exc:
        if exc.status in (410, 451):
            return None, "repository_missing", "GitHub repository is unavailable"
        if exc.status == 404:
            issue, message = await _classify_missing_release(client, owner, repository)
            return None, issue, message
        return None, "upstream_error", str(exc)
    if not isinstance(data, dict):
        return None, "upstream_error", "GitHub returned an invalid release payload"
    return data, None, None


def _select_release_plan(plugin: MarketPlugin, release: dict[str, Any]) -> SelectedReleasePlan:
    if release.get("draft") or release.get("prerelease"):
        return SelectedReleasePlan(
            issue_code="release_missing", reason="GitHub did not return a stable release"
        )
    release_id = str(release.get("id") or "")
    tag = str(release.get("tag_name") or "")
    if not release_id.isdecimal() or not tag or len(tag) > 100:
        return SelectedReleasePlan(
            issue_code="release_missing",
            reason="GitHub did not return a usable release ID and tag",
        )
    assets = release_assets(release)
    if not assets:
        return SelectedReleasePlan(
            issue_code="no_linux_asset",
            reason="Latest release has no compatible Linux package",
        )
    ai = metadata(plugin)
    ai_glob = (
        ai.installation.asset_glob
        if ai and ai.installation and ai.installation.asset_glob != "*"
        else None
    )
    existing_rule = plugin.release_asset_glob or ai_glob
    selected = match_archives(plugin.github_url, assets, existing_rule)
    pattern = existing_rule or suggest_asset_glob(selected, tag)
    if (
        not release_family(selected)
        or pattern is None
        or match_archives(plugin.github_url, assets, pattern) != selected
    ):
        return SelectedReleasePlan(
            issue_code="manual_selection_required",
            reason="Multiple packages or rule mismatch; select a package manually in release sync",
        )
    return SelectedReleasePlan(pattern=pattern, selected=selected)


def _already_synced(
    plugin: MarketPlugin, release: dict[str, Any], pattern: str
) -> MarketReleaseSync | None:
    current = market_release_sync(plugin)
    if current is None or plugin.release_asset_glob != pattern:
        return None
    try:
        verified = verified_snapshot_assets(current, release)
    except GitHubPlanError:
        return None
    if match_archives(plugin.github_url, release_assets(release), pattern) != verified:
        return None
    return current


async def _inspect_snapshot(
    plugin: MarketPlugin, release: dict[str, Any], selected: list[dict[str, Any]]
) -> tuple[MarketReleaseSync | None, ReleaseIssueCode | None, str | None]:
    try:
        snapshot = await _prepare_snapshot(plugin, release, selected)
    except httpx.HTTPError as exc:
        return None, "upstream_error", f"Release archive download network error: {exc}"
    except (GitHubPlanError, PluginPlanError, ValueError) as exc:
        text = str(exc)
        if _is_transient_download_error(text):
            return None, "upstream_error", text
        return None, "archive_invalid", text
    return snapshot, None, None


def _failed_item(
    plugin: MarketPlugin,
    issue_code: ReleaseIssueCode,
    message: str,
    *,
    tag: str | None = None,
) -> EvaluatedListing:
    return EvaluatedListing(
        item=ReleaseSyncItem(
            plugin_id=int(plugin.id),
            title=plugin.title,
            github_url=plugin.github_url,
            action="failed",
            tag=tag,
            issue_code=issue_code,
            cleanable=issue_code != "upstream_error",
            message=message[:2000],
        )
    )


async def evaluate_listing(
    client: GitHubAIClient, plugin: MarketPlugin, *, mode: ReleaseSyncMode
) -> EvaluatedListing:
    plugin_id = int(plugin.id)
    if panel_framework_key(plugin):
        return EvaluatedListing(
            item=ReleaseSyncItem(
                plugin_id=plugin_id,
                title=plugin.title,
                github_url=plugin.github_url,
                action="skipped",
                message="Uses the panel's built-in framework installer",
            )
        )
    try:
        canonical_repo_url(plugin.github_url)
    except ValueError as exc:
        return _failed_item(plugin, "invalid_url", str(exc))
    try:
        validate_installable(plugin)
    except PluginPlanError as exc:
        return _failed_item(plugin, "archive_invalid", str(exc))

    release, issue, error = await _fetch_latest_release(client, plugin.github_url)
    if release is None:
        return _failed_item(plugin, issue or "upstream_error", error or "Release lookup failed")

    tag = str(release.get("tag_name") or "") or None
    plan = _select_release_plan(plugin, release)
    if plan.issue_code is not None or plan.pattern is None or plan.selected is None:
        plan_issue = plan.issue_code or "archive_invalid"
        plan_reason = plan.reason or "Release package selection failed"
        if plan_issue == "manual_selection_required":
            return EvaluatedListing(
                item=ReleaseSyncItem(
                    plugin_id=plugin_id,
                    title=plugin.title,
                    github_url=plugin.github_url,
                    action="skipped",
                    tag=tag,
                    issue_code="manual_selection_required",
                    cleanable=False,
                    message=plan_reason,
                )
            )
        return _failed_item(plugin, plan_issue, plan_reason, tag=tag)

    pattern, selected = plan.pattern, plan.selected
    existing = _already_synced(plugin, release, pattern)
    if existing is not None:
        return EvaluatedListing(
            item=ReleaseSyncItem(
                plugin_id=plugin_id,
                title=plugin.title,
                github_url=plugin.github_url,
                action="unchanged",
                tag=existing.tag,
                rule=pattern,
                selected_assets=[asset.name for asset in existing.assets],
            )
        )

    snapshot, snap_issue, snap_error = await _inspect_snapshot(plugin, release, selected)
    if snapshot is None:
        return _failed_item(
            plugin,
            snap_issue or "archive_invalid",
            snap_error or "Archive inspection failed",
            tag=tag,
        )

    asset_names = [asset.name for asset in snapshot.assets]
    if mode == "scan":
        return EvaluatedListing(
            item=ReleaseSyncItem(
                plugin_id=plugin_id,
                title=plugin.title,
                github_url=plugin.github_url,
                action="unchanged",
                tag=snapshot.tag,
                rule=pattern,
                selected_assets=asset_names,
            )
        )
    return EvaluatedListing(
        item=ReleaseSyncItem(
            plugin_id=plugin_id,
            title=plugin.title,
            github_url=plugin.github_url,
            action="updated",
            tag=snapshot.tag,
            rule=pattern,
            selected_assets=asset_names,
        ),
        release_sync=snapshot.model_dump(mode="json"),
    )


def _terminal_status(job: store.ReleaseSyncJobSnapshot) -> str:
    if job.status == "completed":
        return "success" if job.failed == 0 else "partial"
    if job.status == "cancelled":
        return "cancelled"
    return "failure"


async def _audit_terminal(job: store.ReleaseSyncJobSnapshot | None) -> None:
    if job is None:
        return
    action = "plugin.catalog.scan_invalid" if job.mode == "scan" else "plugin.catalog.sync_releases"
    await record_audit_event(
        category="plugin",
        action=action,
        status=_terminal_status(job),
        actor_user_id=job.actor_user_id,
        details={
            "operation_id": job.operation_id,
            "mode": job.mode,
            "framework": job.framework,
            "total": job.total,
            "processed": job.processed,
            "updated": job.updated,
            "unchanged": job.unchanged,
            "skipped": job.skipped,
            "failed": job.failed,
            "cleanable": job.cleanable,
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


async def _handle_cancelled(job_id: str, mode: ReleaseSyncMode) -> None:
    label = "Invalid plugin scan" if mode == "scan" else "Release sync"
    if await store.is_cancel_requested(job_id):
        await _finish(
            job_id,
            status="cancelled",
            phase="cancelled",
            message=f"{label} cancelled; completed items were retained",
            reason="cancelled",
        )
        return
    await store.requeue_job(job_id, f"{label} interrupted; will resume")


async def _persist_interruption(operation_id: str, mode: ReleaseSyncMode) -> None:
    try:
        await asyncio.shield(_handle_cancelled(operation_id, mode))
    except Exception:
        logger.exception("Failed to persist interrupted release sync %s", operation_id)


async def _process_one(
    client: GitHubAIClient, job: store.ReleaseSyncJobSnapshot, plugin_id: int
) -> bool:
    """Process one target ID. Return False when paused for GitHub rate limiting."""
    target = await store.load_target(plugin_id)
    if target is None:
        await store.record_item(
            job.operation_id,
            ReleaseSyncItem(
                plugin_id=plugin_id,
                title=f"Plugin #{plugin_id}",
                github_url="",
                action="skipped",
                message="Marketplace listing no longer exists",
            ),
        )
        return True
    await store.set_current(job.operation_id, target)
    try:
        evaluated = await evaluate_listing(client, target, mode=job.mode)
    except GitHubRateLimitError as exc:
        retry_at = exc.reset_at or int(time.time() + DEFAULT_RATE_LIMIT_RETRY_SECONDS)
        await store.mark_waiting(
            job.operation_id,
            retry_at=retry_at,
            message="GitHub rate limit reached; the task will resume automatically",
        )
        return False
    await store.record_item(
        job.operation_id,
        evaluated.item,
        release_sync=evaluated.release_sync,
    )
    return True


async def run_job(job: store.ReleaseSyncJobSnapshot) -> None:
    """Run one claimed release sync or cleanup scan job serially."""
    label = "Invalid plugin scan" if job.mode == "scan" else "Release sync"
    try:
        github_token = await store.credentials(job.actor_user_id)
        client = GitHubAIClient(
            github_token,
            require_token=False,
            interval=RELEASE_SYNC_INTERVAL_SECONDS,
        )
    except PermissionError:
        await _finish(
            job.operation_id,
            status="failed",
            phase="failed",
            message=f"Administrator access changed; {label.lower()} stopped",
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
                    message=f"{label} cancelled; completed items were retained",
                    reason="cancelled",
                )
                return
            if not await _process_one(client, job, job.target_ids[index]):
                return

        await _finish(
            job.operation_id,
            status="completed",
            phase="completed",
            message=f"{label} completed",
        )
    except asyncio.CancelledError:
        await _persist_interruption(job.operation_id, job.mode)
        raise
    except GitHubAuthenticationError:
        await _finish(
            job.operation_id,
            status="failed",
            phase="failed",
            message="GitHub token is invalid or expired; check Settings",
            reason="configuration",
        )
    except PermissionError:
        await _finish(
            job.operation_id,
            status="failed",
            phase="failed",
            message="Credentials or administrator access changed; check Settings",
            reason="configuration",
        )
    except Exception:
        logger.exception("%s job %s failed", label, job.operation_id)
        await _finish(
            job.operation_id,
            status="failed",
            phase="failed",
            message=f"{label} failed; completed items were retained",
            reason="execution_error",
        )
    finally:
        await client.close()
