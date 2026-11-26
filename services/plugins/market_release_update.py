"""Decide and inspect updates for installations linked to marketplace listings."""

from __future__ import annotations

from typing import Any

from modules.models import ManagedPlugin, MarketPlugin
from services.linux_runtime_service import steam_runtime_asset_family, steam_runtime_for_asset
from services.plugins.ai_install_policy import metadata
from services.plugins.github_assets import GitHubPlanError
from services.plugins.market_release_package import inspect_market_package
from services.plugins.release_selection import (
    MarketReleaseSync,
    archive_kind,
    compatible_entrypoints,
    fetch_release,
    market_release_sync,
    match_archives,
    newer_market_release,
    release_assets,
    select_release_asset,
    validate_repository_asset,
    verified_snapshot_assets,
)


def _selection_pattern(plugin: MarketPlugin, item: ManagedPlugin) -> str | None:
    if plugin.release_asset_glob:
        return plugin.release_asset_glob
    info = metadata(plugin)
    if info and info.installation and info.installation.asset_glob != "*":
        return info.installation.asset_glob
    if item.installed_asset_name and archive_kind(item.installed_asset_name) != "ordinary":
        return None
    return item.asset_glob


async def _old_entrypoints(
    plugin: MarketPlugin, item: ManagedPlugin, token: str | None
) -> list[str]:
    if not item.installed_asset_name or item.installed_version in {"", "unknown"}:
        return []
    snapshot = market_release_sync(plugin)
    if snapshot and snapshot.tag == item.installed_version:
        for asset in snapshot.assets:
            if asset.name == item.installed_asset_name:
                return asset.entrypoints
    old = await fetch_release(plugin.github_url, token, tag=item.installed_version)
    for asset in release_assets(old, manual=True):
        if asset["name"] == item.installed_asset_name:
            validate_repository_asset(plugin.github_url, asset["url"])
            return (await inspect_market_package(plugin, old, asset))["entrypoints"]
    return []


def _select_candidate(
    plugin: MarketPlugin,
    item: ManagedPlugin,
    release: dict[str, Any],
    profile: dict[str, Any] | None,
    pinned: MarketReleaseSync | None,
) -> tuple[dict[str, Any], bool]:
    if pinned is not None:
        return select_release_asset(verified_snapshot_assets(pinned, release), profile), False
    assets = release_assets(release)
    pattern = _selection_pattern(plugin, item)
    matches = (
        match_archives(plugin.github_url, assets, pattern) if pattern not in {None, "*"} else []
    )
    # Older installations store a glob derived from the selected RT3/RT4 asset.
    if not plugin.release_asset_glob and len(matches) == 1:
        family = steam_runtime_asset_family(matches[0]["name"])
        if family:
            matches = [
                asset for asset in assets if steam_runtime_asset_family(asset["name"]) == family
            ]
    if matches:
        return select_release_asset(matches, profile), False
    return select_release_asset(assets, profile), True


async def resolve_market_update(
    plugin: MarketPlugin,
    item: ManagedPlugin,
    release: dict[str, Any],
    token: str | None,
    profile: dict[str, Any] | None,
    *,
    pinned: MarketReleaseSync | None = None,
) -> dict[str, Any]:
    tag = str(release.get("tag_name") or "")
    release_id = str(release.get("id") or "")
    if not tag or not release_id or release.get("draft") or release.get("prerelease"):
        raise GitHubPlanError("GitHub did not return a stable release")
    same_release = bool(item.installed_release_id == release_id or item.installed_version == tag)
    asset_name = item.installed_asset_name or ""
    installed_runtime = steam_runtime_for_asset(asset_name)
    recommended_runtime = (profile or {}).get("recommended_steam_runtime")
    keep_installed = same_release and (
        not installed_runtime or not recommended_runtime or installed_runtime == recommended_runtime
    )
    result: dict[str, Any] = {"release_id": release_id, "version": tag}
    if keep_installed or (
        not same_release
        and not await newer_market_release(
            plugin.github_url, token, item.installed_version, release
        )
    ):
        return {**result, "asset": {"name": asset_name}, "no_op": True}

    asset, renamed = _select_candidate(plugin, item, release, profile, pinned)
    validate_repository_asset(plugin.github_url, asset["url"])
    package = await inspect_market_package(plugin, release, asset)
    if pinned:
        expected = next(value.sha256 for value in pinned.assets if value.id == asset["id"])
        if package["archive_sha256"] != expected:
            raise GitHubPlanError("The synchronized archive changed; preview it again")
    elif renamed and not compatible_entrypoints(
        await _old_entrypoints(plugin, item, token), package["entrypoints"]
    ):
        raise GitHubPlanError(
            f"Unable to verify renamed package {asset['name']!r}; review it in the plugin marketplace"
        )
    return {
        **result,
        "asset": {**asset, "browser_download_url": asset["url"]},
        "market_package": package,
    }
