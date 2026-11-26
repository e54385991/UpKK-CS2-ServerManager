"""Select Linux GitHub release assets for market plugin installs."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from modules import MarketPlugin, Server, User
from services.compat import LateBoundModule
from services.plugins.ai_install_policy import select_assets
from services.plugins.common import PluginPlanError
from services.plugins.github_assets import GitHubPlanError
from services.plugins.market_release_package import inspect_market_package
from services.plugins.release_selection import (
    ARCHIVE_EXTENSIONS,
    match_archives,
    release_assets,
    release_family,
    select_release_asset,
)

host = LateBoundModule("services.plugin_conflict_service")

# Kept for the original service facade's exports.
_ARCHIVE_EXTENSIONS = ARCHIVE_EXTENSIONS
_BLOCKED_ASSET_MARKERS = ("windows", "macos", "source-code", "symbols", "debug", "relwithdebinfo")


def _release_asset_candidates(release: dict[str, Any]) -> list[dict[str, Any]]:
    return release_assets(release)


async def _latest_release_asset(
    db: AsyncSession,
    plugin: MarketPlugin,
    server: Server,
    user: User,
    linux_runtime_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    match = host._GITHUB_REPOSITORY.fullmatch(plugin.github_url.strip().rstrip("/"))
    if not match:
        raise PluginPlanError(f"Invalid GitHub URL for {plugin.title}")
    owner, repository = match.groups()
    token = await host.get_effective_github_token(db, user)
    success, data, error = await host.http_helper.get(
        f"https://api.github.com/repos/{owner}/{repository}/releases/latest",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "CS2-ServerManager",
        },
        timeout=30,
        proxy=server.github_proxy,
        github_token=token,
    )
    if not success or not isinstance(data, dict):
        raise PluginPlanError(f"Failed to fetch {plugin.title} release: {error}")
    if data.get("draft") or data.get("prerelease"):
        raise PluginPlanError(f"No stable release found for {plugin.title}")
    candidates = host._release_asset_candidates(data)
    pattern = getattr(plugin, "release_asset_glob", None)
    candidates = (
        match_archives(plugin.github_url, candidates, pattern)
        if pattern
        else select_assets(plugin, candidates)
    )
    if not candidates:
        raise PluginPlanError(f"No suitable Linux release asset found for {plugin.title}")
    if release_family(candidates):
        try:
            candidates = [select_release_asset(candidates, linux_runtime_profile)]
        except GitHubPlanError as exc:
            raise PluginPlanError(str(exc)) from exc

    valid: list[dict[str, Any]] = []
    rejected: list[str] = []
    for asset in candidates:
        try:
            valid.append(await inspect_market_package(plugin, data, asset))
        except (GitHubPlanError, PluginPlanError) as exc:
            rejected.append(f"{asset['name']}: {exc}")
        if len(valid) > 1:
            names = ", ".join(item["asset_name"] for item in valid)
            raise PluginPlanError(f"Multiple compatible packages for {plugin.title}: {names}")
    if valid:
        return valid[0]
    raise PluginPlanError(
        f"No installable CS2 release asset found for {plugin.title}: " + "; ".join(rejected[:3])
    )


def _asset_from_download_url(plugin: MarketPlugin, download_url: str) -> dict[str, Any]:
    """Build an install asset from a caller-selected GitHub release URL."""
    from services.linux_runtime_service import steam_runtime_for_asset
    from services.plugins.market_assets import requested_release

    selected_release_id = ""
    selected_release_tag = "unknown"
    selected_asset_name = download_url.rsplit("/", 1)[-1]
    try:
        release_id, tag, asset_name = requested_release(download_url)
    except ValueError:
        release_id = tag = asset_name = None
    if release_id and tag and asset_name:
        selected_release_id = release_id
        selected_release_tag = tag
        selected_asset_name = asset_name
    custom_target = plugin.custom_install_path
    return {
        "download_url": download_url,
        "release_id": selected_release_id,
        "release_tag": selected_release_tag,
        "asset_name": selected_asset_name,
        "steam_runtime": steam_runtime_for_asset(selected_asset_name),
        "archive_sha256": None,
        "source_prefix": None,
        "custom_install_path": custom_target,
        "allowed_roots": [] if custom_target is not None else ["addons", "cfg"],
    }
