"""Resolve a marketplace listing to a GitHub release asset."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from modules.http_helper import http_helper
from modules.models import MarketPlugin, Server, User
from services.github_credentials import get_effective_github_token
from services.github_url import parse_github_url
from services.linux_runtime_service import RuntimeSelectionRequired
from services.plugins.github_assets import GitHubPlanError
from services.plugins.release_selection import match_archives, release_assets, select_release_asset

WINDOWS_ASSET_MARKERS = ("windows", "-win-", "_win_")


def requested_release(
    download_url: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Parse a GitHub release download URL into ``(release_id, tag, asset)``."""
    if not download_url:
        return None, None, None
    if (
        not download_url.startswith("https://github.com/")
        or "/releases/download/" not in download_url
    ):
        raise ValueError("Invalid download URL. Must be a GitHub releases download URL.")
    release_parts = download_url.split("/releases/download/", 1)[1].split("/", 1)
    if len(release_parts) != 2:
        return None, None, None
    tag, asset = release_parts
    return f"tag:{tag}", tag, asset


def is_windows_release_asset(asset_name: str) -> bool:
    """Return True when a GitHub asset name is clearly a Windows package."""
    lowered = asset_name.casefold()
    return any(marker in lowered for marker in WINDOWS_ASSET_MARKERS) or lowered.endswith(
        "-win.zip"
    )


async def resolve_latest_market_asset(
    plugin: MarketPlugin,
    server: Server,
    db: AsyncSession,
    current_user: User,
    linux_runtime_profile: dict | None,
    *,
    parse_url=parse_github_url,
    token_getter=get_effective_github_token,
    http=http_helper,
) -> tuple[Optional[dict], Optional[str]]:
    """Resolve the latest compatible archive before dependencies change the server."""
    owner, repo = parse_url(plugin.github_url)
    github_token = await token_getter(db, current_user)
    success, data, error = await http.get(
        f"https://api.github.com/repos/{owner}/{repo}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "CS2-ServerManager"},
        timeout=30,
        proxy=server.github_proxy,
        github_token=github_token,
    )
    if not success or not isinstance(data, dict):
        return None, f"Failed to fetch latest release: {error}"

    if data.get("draft") or data.get("prerelease"):
        return None, "No suitable stable release found for installation"
    candidates = match_archives(
        plugin.github_url, release_assets(data), getattr(plugin, "release_asset_glob", None)
    )
    if not candidates:
        empty_url = any(
            isinstance(asset, dict) and not asset.get("browser_download_url")
            for asset in data.get("assets") or []
        )
        if empty_url:
            return None, "Selected release asset does not include a download URL"
        return None, "No suitable release asset found for installation"
    try:
        selected = select_release_asset(candidates, linux_runtime_profile)
    except GitHubPlanError as exc:
        raise RuntimeSelectionRequired(str(exc)) from exc
    download_url = selected["url"]
    return (
        {
            "download_url": download_url,
            "release_id": str(data.get("id") or ""),
            "release_tag": str(data.get("tag_name") or "unknown"),
            "asset_name": str(selected.get("name") or ""),
        },
        None,
    )


async def resolve_market_asset(
    plugin: MarketPlugin,
    server: Server,
    db: AsyncSession,
    current_user: User,
    download_url: str | None,
    selected_asset_name: str | None,
    runtime_profile,
    *,
    resolve_latest=resolve_latest_market_asset,
):
    """Return a concrete download URL, or an error string when none is suitable."""
    if download_url:
        return download_url, None, None, selected_asset_name, None, runtime_profile

    from services.linux_runtime_service import detect_linux_runtime_profile

    runtime_profile = await detect_linux_runtime_profile(server)
    asset, error = await resolve_latest(plugin, server, db, current_user, runtime_profile)
    if asset is None:
        return (
            None,
            None,
            None,
            None,
            error or "No suitable release asset found for installation",
            runtime_profile,
        )
    return (
        asset["download_url"],
        asset["release_id"],
        asset["release_tag"],
        asset["asset_name"],
        None,
        runtime_profile,
    )
