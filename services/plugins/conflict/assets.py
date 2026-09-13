"""Select Linux GitHub release assets for market plugin installs."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from modules import MarketPlugin, Server, User
from services.compat import LateBoundModule
from services.plugins.ai_install_policy import apply_layout, metadata, select_assets
from services.plugins.common import PluginPlanError

host = LateBoundModule("services.plugin_conflict_service")

_ARCHIVE_EXTENSIONS = (".zip", ".tar.gz", ".tgz", ".tar", ".7z")
_BLOCKED_ASSET_MARKERS = (
    "windows",
    "win32",
    "win64",
    "-win-",
    "_win_",
    "macos",
    "osx",
    "source code",
    "source-code",
    "symbols",
    "debug",
)


def _release_asset_candidates(release: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for asset in release.get("assets", []):
        name = str(asset.get("name") or "")
        lowered = name.casefold()
        download_url = str(asset.get("browser_download_url") or "")
        if (
            not lowered.endswith(_ARCHIVE_EXTENSIONS)
            or not download_url
            or any(marker in lowered for marker in _BLOCKED_ASSET_MARKERS)
        ):
            continue
        candidates.append(
            {
                "id": str(asset.get("id") or ""),
                "name": name,
                "url": download_url,
                "size": int(asset.get("size") or 0),
                "digest": asset.get("digest"),
                "content_type": asset.get("content_type"),
            }
        )

    def rank(asset: dict[str, Any]) -> tuple[int, int, int, str]:
        lowered = asset["name"].casefold()
        upgrade_only = any(marker in lowered for marker in ("upgrade", "update-only"))
        linux_named = "linux" in lowered
        runtime_named = any(
            marker in lowered for marker in ("runtime", "release", "server", "plugin")
        )
        return (
            1 if upgrade_only else 0,
            0 if linux_named else 1,
            0 if runtime_named else 1,
            lowered,
        )

    return sorted(candidates, key=rank)


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
    candidates = select_assets(plugin, host._release_asset_candidates(data))
    if not candidates:
        raise PluginPlanError(f"No suitable Linux release asset found for {plugin.title}")

    from services.linux_runtime_service import (
        RuntimeSelectionRequired,
        has_paired_runtime_assets,
        select_unique_runtime_asset,
        steam_runtime_for_asset,
    )

    try:
        selected_runtime_asset = select_unique_runtime_asset(candidates, linux_runtime_profile)
    except RuntimeSelectionRequired as exc:
        raise PluginPlanError(f"{plugin.title}: {exc}") from exc
    if has_paired_runtime_assets(candidates):
        if selected_runtime_asset is None:
            raise PluginPlanError(
                f"{plugin.title}: multiple Steam Runtime package families are available; "
                "select a release asset explicitly"
            )
        candidates = [selected_runtime_asset]

    from services.github_plugin_plan_service import (
        GitHubPlanError,
        inspect_release_asset_layout,
    )

    rejected: list[str] = []
    for asset in candidates:
        try:
            layout = apply_layout(plugin, await inspect_release_asset_layout(asset, repository))
        except (GitHubPlanError, PluginPlanError) as exc:
            rejected.append(f"{asset['name']}: {exc}")
            continue
        mapping = layout["mapping"]
        if layout["mapping_required"] and not plugin.custom_install_path:
            rejected.append(f"{asset['name']}: archive layout is not a recognized CS2 plugin")
            continue

        target_prefixes = sorted({item["target"].split("/", 1)[0] for item in mapping})
        inferred_custom_target = None
        if len(mapping) == 1 and (
            mapping[0]["target"] not in {"addons", "cfg"}
            or mapping[0].get("source", ".") == (layout["source_prefix"] or ".")
        ):
            inferred_custom_target = mapping[0]["target"]
        info = metadata(plugin)
        custom_target = (
            inferred_custom_target
            if (info and info.installation)
            or (not layout["mapping_required"] and inferred_custom_target is None)
            else plugin.custom_install_path or inferred_custom_target
        )
        if layout.get("archive_mappings"):
            custom_target = None
        return {
            "archive_mappings": layout.get("archive_mappings", []),
            "download_url": asset["url"],
            "release_id": str(data.get("id") or ""),
            "release_tag": str(data.get("tag_name") or "unknown"),
            "asset_name": asset["name"],
            "steam_runtime": steam_runtime_for_asset(asset["name"]),
            "archive_sha256": layout["archive_sha256"],
            "source_prefix": layout["source_prefix"],
            "custom_install_path": custom_target,
            "allowed_roots": (
                []
                if custom_target is not None
                else [root for root in target_prefixes if root in {"addons", "cfg"}]
            ),
        }
    detail = "; ".join(rejected[:3])
    raise PluginPlanError(
        f"No installable CS2 release asset found for {plugin.title}"
        + (f": {detail}" if detail else "")
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
