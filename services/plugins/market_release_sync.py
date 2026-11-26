"""Administrator preview, confirmation and eligible server lookup."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlmodel import col, select

from modules.database import async_session_maker
from modules.models import ManagedPlugin, MarketPlugin, Server, User
from services.github_credentials import get_effective_github_token
from services.plugins.ai_install_policy import metadata
from services.plugins.github_assets import GitHubPlanError
from services.plugins.market_release_package import inspect_market_package
from services.plugins.panel_frameworks import panel_framework_key
from services.plugins.release_selection import (
    MarketReleaseSync,
    SyncedAsset,
    archive_kind,
    fetch_release,
    market_release_sync,
    match_archives,
    release_assets,
    release_family,
    suggest_asset_glob,
    validate_repository_asset,
    verified_snapshot_assets,
)
from services.plugins.tracking import canonical_repo_url
from services.redis_manager import redis_manager

_PREVIEW_TTL = 900


class ReleaseListingMissing(LookupError):
    pass


class ReleaseSyncUnavailable(RuntimeError):
    pass


def _state(plugin: MarketPlugin) -> dict[str, object]:
    updated_at = plugin.updated_at
    return {
        "repository": plugin.github_url,
        "updated_at": updated_at.isoformat() if updated_at else None,
        "rule": plugin.release_asset_glob,
        "snapshot": plugin.release_sync,
    }


async def _load_listing(plugin_id: int, user: User) -> tuple[MarketPlugin, str | None]:
    async with async_session_maker() as db:
        plugin = await db.get(MarketPlugin, plugin_id)
        if plugin is None:
            raise ReleaseListingMissing("Marketplace plugin not found")
        try:
            canonical_repo_url(plugin.github_url)
        except ValueError as exc:
            raise GitHubPlanError(str(exc)) from exc
        if panel_framework_key(plugin):
            raise GitHubPlanError("This framework uses the panel's framework updater")
        return plugin, await get_effective_github_token(db, user)


def _preview_candidates(
    plugin: MarketPlugin,
    assets: list[dict[str, Any]],
    pattern: str | None,
    asset_name: str | None,
) -> list[dict[str, Any]]:
    if asset_name:
        selected = next((asset for asset in assets if asset["name"] == asset_name), None)
        if selected is None:
            raise GitHubPlanError("Choose an ordinary Linux release package")
        from services.linux_runtime_service import steam_runtime_asset_family

        family = steam_runtime_asset_family(asset_name)
        return (
            [asset for asset in assets if steam_runtime_asset_family(asset["name"]) == family]
            if family
            else [selected]
        )
    return match_archives(plugin.github_url, assets, pattern)


async def _prepare_snapshot(
    plugin: MarketPlugin, release: dict[str, Any], selected: list[dict[str, Any]]
) -> MarketReleaseSync:
    release_id = str(release.get("id") or "")
    tag = str(release.get("tag_name") or "")
    if not release_id.isdecimal() or not tag or len(tag) > 100:
        raise GitHubPlanError("GitHub did not return a usable release ID and tag")
    verified = []
    for asset in selected:
        validate_repository_asset(plugin.github_url, asset["url"])
        if not asset["id"].isdecimal():
            raise GitHubPlanError(f"Missing GitHub asset ID for {asset['name']}")
        package = await inspect_market_package(plugin, release, asset)
        verified.append(
            SyncedAsset(
                id=asset["id"],
                name=asset["name"],
                url=asset["url"],
                sha256=package["archive_sha256"],
                entrypoints=package["entrypoints"],
            )
        )
    return MarketReleaseSync(
        revision=uuid4().hex,
        repository=canonical_repo_url(plugin.github_url).casefold(),
        release_id=release_id,
        tag=tag,
        assets=verified,
    )


async def preview_release_sync(
    plugin_id: int, user: User, *, rule: str | None, asset_name: str | None
) -> dict[str, Any]:
    plugin, token = await _load_listing(plugin_id, user)
    release = await fetch_release(plugin.github_url, token)
    if release.get("draft") or release.get("prerelease"):
        raise GitHubPlanError("GitHub did not return a stable release")
    assets = release_assets(release)
    all_assets = release_assets(release, manual=True)
    previous = market_release_sync(plugin)
    ai = metadata(plugin)
    existing_rule = plugin.release_asset_glob or (
        ai.installation.asset_glob if ai and ai.installation else None
    )
    selected = _preview_candidates(plugin, assets, rule or existing_rule, asset_name)
    pattern = rule or (
        suggest_asset_glob(selected, str(release.get("tag_name") or ""))
        if asset_name
        else existing_rule
    )
    pattern = pattern or suggest_asset_glob(selected, str(release.get("tag_name") or ""))
    snapshot = None
    reason = None
    if not release_family(selected):
        reason = "Choose one ordinary Linux package or a single Steam Runtime family"
    elif pattern is None or match_archives(plugin.github_url, assets, pattern) != selected:
        reason = "The rule must match exactly the selected package or Steam Runtime family"
    else:
        try:
            snapshot = await _prepare_snapshot(plugin, release, selected)
        except (GitHubPlanError, ValueError) as exc:
            reason = str(exc)
    if snapshot is not None and not await redis_manager.set(
        f"market-release-preview:{user.id}:{plugin_id}:{snapshot.revision}",
        {"snapshot": snapshot.model_dump(mode="json"), "rule": pattern, "listing": _state(plugin)},
        expire=_PREVIEW_TTL,
    ):
        raise ReleaseSyncUnavailable("Release preview could not be saved. Try again")
    return {
        "previous_tag": previous.tag if previous else plugin.version,
        "previous_assets": [asset.name for asset in previous.assets] if previous else [],
        "tag": str(release.get("tag_name") or ""),
        "assets": [
            {"name": asset["name"], "ordinary": archive_kind(asset["name"]) == "ordinary"}
            for asset in all_assets
        ],
        "selected_assets": [asset["name"] for asset in selected] if snapshot else [],
        "rule": pattern,
        "preview_id": snapshot.revision if snapshot else None,
        "reason": reason,
    }


async def save_release_sync(plugin_id: int, user: User, preview_id: str) -> MarketReleaseSync:
    data = await redis_manager.get(f"market-release-preview:{user.id}:{plugin_id}:{preview_id}")
    if not isinstance(data, dict):
        raise GitHubPlanError("Release preview expired; preview it again")
    snapshot = MarketReleaseSync.model_validate(data["snapshot"])
    plugin, token = await _load_listing(plugin_id, user)
    release = await fetch_release(plugin.github_url, token)
    verified = verified_snapshot_assets(snapshot, release)
    if match_archives(plugin.github_url, release_assets(release), data["rule"]) != verified:
        raise GitHubPlanError("The release rule matches different files; preview it again")
    async with async_session_maker() as db:
        saved = await db.get(MarketPlugin, plugin_id, with_for_update=True)
        if saved is None:
            raise ReleaseListingMissing("Marketplace plugin not found")
        current = market_release_sync(saved)
        if (
            current is not None
            and saved.release_asset_glob == data["rule"]
            and current.model_dump(exclude={"revision"})
            == snapshot.model_dump(exclude={"revision"})
        ):
            return current
        if _state(saved) != data["listing"]:
            raise GitHubPlanError("Marketplace listing changed; preview the release again")
        saved.release_asset_glob = data["rule"]
        saved.release_sync = snapshot.model_dump(mode="json")
        await db.commit()
    return snapshot


async def release_targets(
    plugin_id: int, user: User, server_ids: list[int] | None = None
) -> tuple[MarketReleaseSync, list[dict[str, Any]]]:
    async with async_session_maker() as db:
        plugin = await db.get(MarketPlugin, plugin_id)
        if plugin is None:
            raise ReleaseListingMissing("Marketplace plugin not found")
        snapshot = market_release_sync(plugin)
        if snapshot is None:
            raise GitHubPlanError("Synchronize a marketplace release first")
        query = (
            select(
                Server.id,
                Server.name,
                ManagedPlugin,
            )
            .join(ManagedPlugin, col(ManagedPlugin.server_id) == col(Server.id))
            .where(
                col(ManagedPlugin.market_plugin_id) == plugin_id,
                col(ManagedPlugin.source_type) == "market",
                col(ManagedPlugin.framework_key).is_(None),
            )
            .order_by(col(Server.name), col(Server.id))
        )
        if not user.is_admin:
            query = query.where(col(Server.user_id) == user.id)
        if server_ids is not None:
            query = query.where(col(Server.id).in_(server_ids))
        rows = (await db.execute(query)).all()
    return snapshot, [
        {
            "server_id": server_id,
            "server_name": server_name,
            "managed_plugin_id": item.id,
            "installed_version": item.installed_version,
            "installed_asset_name": item.installed_asset_name,
            "backup_before_update": item.backup_before_update,
            "restart_after_update": item.restart_after_update,
        }
        for server_id, server_name, item in rows
    ]
