"""Inspected marketplace package and the corresponding deployment parameters."""

from __future__ import annotations

from typing import Any

from modules.models import MarketPlugin
from services.linux_runtime_service import steam_runtime_for_asset
from services.plugins.ai_install_policy import apply_layout, metadata
from services.plugins.common import PluginPlanError
from services.plugins.release_selection import mapped_entrypoints


async def inspect_market_package(
    plugin: MarketPlugin, release: dict[str, Any], asset: dict[str, Any]
) -> dict[str, Any]:
    from services.github_plugin_plan_service import inspect_release_asset_layout

    repository = plugin.github_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    layout = apply_layout(plugin, await inspect_release_asset_layout(asset, repository))
    if layout["mapping_required"] and not plugin.custom_install_path:
        raise PluginPlanError(f"{asset['name']}: archive layout is not a recognized CS2 plugin")

    mapping = layout["mapping"]
    targets = sorted({item["target"].split("/", 1)[0] for item in mapping})
    inferred = None
    if len(mapping) == 1 and (
        mapping[0]["target"] not in {"addons", "cfg"}
        or mapping[0].get("source", ".") == (layout["source_prefix"] or ".")
    ):
        inferred = mapping[0]["target"]
    info = metadata(plugin)
    if (info and info.installation) or (not layout["mapping_required"] and not inferred):
        target = inferred
    else:
        target = plugin.custom_install_path or inferred
    archive_mappings = layout.get("archive_mappings", [])
    if archive_mappings:
        target = None
    digest = layout["archive_sha256"]
    advertised_digest = asset.get("digest")
    if advertised_digest and str(advertised_digest).casefold() != f"sha256:{digest}".casefold():
        raise PluginPlanError(f"GitHub archive digest changed for {asset['name']}")
    return {
        "archive_mappings": archive_mappings,
        "download_url": asset["url"],
        "release_id": str(release.get("id") or ""),
        "release_tag": str(release.get("tag_name") or "unknown"),
        "asset_name": asset["name"],
        "steam_runtime": steam_runtime_for_asset(asset["name"]),
        "archive_sha256": digest,
        "source_prefix": layout["source_prefix"],
        "custom_install_path": target,
        "allowed_roots": []
        if target is not None
        else [root for root in targets if root in {"addons", "cfg"}],
        "entrypoints": mapped_entrypoints(layout, target=target),
    }
