"""Release browsing for manual market installs with a shared default package."""

from __future__ import annotations

import asyncio
from typing import Any

from modules.http_helper import http_helper
from modules.models import MarketPlugin
from services.linux_runtime_service import annotate_runtime_assets
from services.plugins.ai_install_policy import metadata
from services.plugins.github_assets import GitHubPlanError
from services.plugins.release_selection import (
    archive_kind,
    fetch_release,
    match_archives,
    release_assets,
    select_release_asset,
)
from services.plugins.tracking import repo_api_url


async def _release_history(repo: str, token: str | None, count: int) -> list[dict[str, Any]]:
    success, data, error = await http_helper.get(
        repo_api_url(repo).removesuffix("/latest"),
        params={"per_page": count},
        headers={"Accept": "application/vnd.github+json", "User-Agent": "CS2-ServerManager"},
        timeout=30,
        github_token=token,
    )
    if not success or not isinstance(data, list):
        raise GitHubPlanError(f"Failed to fetch releases: {error or 'invalid response'}")
    return [
        item
        for item in data
        if isinstance(item, dict) and not item.get("draft") and not item.get("prerelease")
    ]


async def _stable_release(repo: str, token: str | None) -> dict[str, Any] | None:
    try:
        latest = await fetch_release(repo, token)
    except GitHubPlanError:
        return None
    return None if latest.get("draft") or latest.get("prerelease") else latest


async def market_release_choices(
    plugin: MarketPlugin,
    token: str | None,
    profile: dict[str, Any] | None,
    *,
    count: int,
) -> list[dict[str, Any]]:
    latest, history = await asyncio.gather(
        _stable_release(plugin.github_url, token),
        _release_history(plugin.github_url, token, count),
    )

    ordinary_rule = plugin.release_asset_glob
    if not ordinary_rule and (info := metadata(plugin)) and info.installation:
        ordinary_rule = info.installation.asset_glob

    choices = []
    latest_id = str(latest.get("id") or "") if latest else None
    if latest is not None:
        choices.append(_release_choice(latest, profile, plugin.github_url, ordinary_rule))
    for release in history:
        if str(release.get("id") or "") != latest_id:
            choice = _release_choice(release, profile, plugin.github_url, None)
            if choice["assets"]:
                choices.append(choice)
    if latest is None and choices:
        choices[0]["default_asset_name"] = None
    return choices[:count]


def _release_choice(
    release: dict[str, Any],
    profile: dict[str, Any] | None,
    repo: str,
    rule: str | None,
) -> dict[str, Any]:
    manual = release_assets(release, manual=True)
    ordinary = [asset for asset in manual if archive_kind(asset["name"]) == "ordinary"]
    try:
        default_name = select_release_asset(match_archives(repo, ordinary, rule), profile)["name"]
    except GitHubPlanError:
        default_name = None
    return {
        "id": str(release.get("id") or ""),
        "tag_name": release.get("tag_name"),
        "name": release.get("name"),
        "published_at": release.get("published_at"),
        "prerelease": bool(release.get("prerelease")),
        "default_asset_name": default_name,
        "assets": annotate_runtime_assets(
            [
                {
                    "name": asset["name"],
                    "browser_download_url": asset["url"],
                    "size": asset["size"],
                    "content_type": asset["content_type"],
                }
                for asset in manual
            ],
            profile,
        ),
    }
