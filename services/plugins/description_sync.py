"""Refresh marketplace descriptions from the upstream GitHub READMEs.

The admin console renders a listing's description as Markdown, so the useful
long form is the repository README. This service re-fetches it for many
listings at once instead of making an administrator open each plugin.

GitHub is contacted outside any open database transaction: the caller's read
is committed first, the bounded-concurrency fetch runs, and only then are the
rows written back.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from modules.http_helper import http_helper
from modules.models.plugins import MarketPlugin, PluginFramework
from services.github_service import parse_github_url
from services.plugins.github_readme import decode_readme
from services.plugins.types import DescriptionSyncItem, DescriptionSyncResult

logger = logging.getLogger(__name__)

# One request refreshes at most this many listings so an administrator action
# stays a bounded HTTP call. The response reports what is left to do.
MAX_DESCRIPTION_SYNC_PLUGINS = 200

# GitHub rate limits unauthenticated callers hard; keep the fan-out small.
SYNC_CONCURRENCY = 4

_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "CS2-ServerManager",
    "X-GitHub-Api-Version": "2022-11-28",
}


async def _fetch_readme(
    github_url: str,
    *,
    github_token: str | None,
    github_proxy: str | None,
) -> tuple[str | None, str | None]:
    """Return ``(readme, error)`` for one repository."""
    try:
        owner, repo = parse_github_url(github_url)
    except ValueError as exc:
        return None, str(exc)

    success, data, error = await http_helper.get(
        f"https://api.github.com/repos/{owner}/{repo}/readme",
        headers=_GITHUB_HEADERS,
        timeout=30,
        proxy=github_proxy,
        github_token=github_token,
    )
    if not success or not isinstance(data, dict):
        return None, f"Failed to fetch README: {error}"
    readme = decode_readme(str(data.get("content", "")))
    if not readme:
        return None, None
    return readme, None


async def _load_targets(
    db: AsyncSession,
    plugin_ids: list[int] | None,
    framework: PluginFramework | None,
) -> list[MarketPlugin]:
    query = select(MarketPlugin).order_by(col(MarketPlugin.id))
    if plugin_ids:
        query = query.where(col(MarketPlugin.id).in_(list(dict.fromkeys(plugin_ids))))
    if framework is not None:
        query = query.where(MarketPlugin.framework == framework)
    result = await db.execute(query)
    return list(result.scalars().all())


async def sync_market_plugin_descriptions(
    db: AsyncSession,
    *,
    github_token: str | None = None,
    github_proxy: str | None = None,
    plugin_ids: list[int] | None = None,
    framework: PluginFramework | None = None,
    overwrite: bool = True,
) -> DescriptionSyncResult:
    """Re-fetch READMEs and write them back as marketplace descriptions.

    ``overwrite=False`` only fills listings that have no description yet.
    """
    candidates = await _load_targets(db, plugin_ids, framework)
    remaining = max(0, len(candidates) - MAX_DESCRIPTION_SYNC_PLUGINS)
    targets = candidates[:MAX_DESCRIPTION_SYNC_PLUGINS]

    items: list[DescriptionSyncItem] = []
    pending: list[MarketPlugin] = []
    for plugin in targets:
        if not overwrite and (plugin.description or "").strip():
            items.append(
                DescriptionSyncItem(
                    plugin_id=int(plugin.id),
                    title=plugin.title,
                    github_url=plugin.github_url,
                    action="skipped",
                    message="Description already set",
                )
            )
            continue
        pending.append(plugin)

    # Release the read transaction before the external GitHub requests.
    await db.commit()

    semaphore = asyncio.Semaphore(SYNC_CONCURRENCY)

    async def fetch(plugin: MarketPlugin) -> tuple[str | None, str | None]:
        async with semaphore:
            return await _fetch_readme(
                plugin.github_url,
                github_token=github_token,
                github_proxy=github_proxy,
            )

    fetched = await asyncio.gather(*(fetch(plugin) for plugin in pending))

    changed = 0
    for plugin, (readme, error) in zip(pending, fetched, strict=True):
        plugin_id = int(plugin.id)
        if error:
            logger.warning(f"Description sync failed for plugin {plugin_id}: {error}")
            items.append(
                DescriptionSyncItem(
                    plugin_id=plugin_id,
                    title=plugin.title,
                    github_url=plugin.github_url,
                    action="failed",
                    message=error,
                )
            )
            continue
        if not readme:
            items.append(
                DescriptionSyncItem(
                    plugin_id=plugin_id,
                    title=plugin.title,
                    github_url=plugin.github_url,
                    action="skipped",
                    message="Repository has no README",
                )
            )
            continue
        if (plugin.description or "") == readme:
            items.append(
                DescriptionSyncItem(
                    plugin_id=plugin_id,
                    title=plugin.title,
                    github_url=plugin.github_url,
                    action="unchanged",
                )
            )
            continue
        plugin.description = readme
        db.add(plugin)
        changed += 1
        items.append(
            DescriptionSyncItem(
                plugin_id=plugin_id,
                title=plugin.title,
                github_url=plugin.github_url,
                action="updated",
            )
        )

    if changed:
        await db.commit()

    return DescriptionSyncResult(
        total=len(items),
        updated=sum(1 for item in items if item.action == "updated"),
        unchanged=sum(1 for item in items if item.action == "unchanged"),
        skipped=sum(1 for item in items if item.action == "skipped"),
        failed=sum(1 for item in items if item.action == "failed"),
        remaining=remaining,
        items=items,
    )
