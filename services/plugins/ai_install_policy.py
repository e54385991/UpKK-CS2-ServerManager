"""Validated AI metadata consumed by marketplace installs and upgrades."""

from fnmatch import fnmatchcase

from modules.models import ManagedPlugin, MarketPlugin
from modules.plugin_ai import PluginAIInfo
from modules.schemas.plugins import GitHubPluginInstallRequest
from services.plugins.common import PluginPlanError


def metadata(plugin: MarketPlugin) -> PluginAIInfo | None:
    if plugin.ai_metadata is None:
        return None
    try:
        return PluginAIInfo.model_validate(plugin.ai_metadata)
    except ValueError as exc:
        raise PluginPlanError(
            f"{plugin.title}: invalid AI installation settings; review required"
        ) from exc


def validate_installable(plugin: MarketPlugin) -> None:
    """Reject only metadata the panel cannot parse at all.

    Outstanding prerequisites and notes used to abort the preflight here, which
    made most AI-imported listings permanently uninstallable ("AI installation
    settings have unresolved requirements; administrator review required").
    They are surfaced as install-time notices instead — see
    ``install_notice`` and ``services.plugins.ai_requirements`` — while a
    missing install rule simply falls back to the normal archive auto-detection
    every non-AI listing already uses.
    """
    metadata(plugin)


def install_notice(plugin: MarketPlugin) -> dict[str, object] | None:
    """Prerequisites and notes to show before installing ``plugin``, if any."""
    info = metadata(plugin)
    if info is None:
        return None
    notes = list(info.notes)
    if info.installation is None:
        notes.append("No reviewed installation rule; the archive layout is detected automatically")
    if not info.requirements and not notes:
        return None
    return {
        "plugin_id": int(plugin.id or 0),
        "title": plugin.title,
        "reviewed": info.reviewed,
        "requirements": list(info.requirements),
        "notes": notes,
    }


def select_assets(plugin: MarketPlugin, candidates: list[dict]) -> list[dict]:
    info = metadata(plugin)
    if not info or not info.installation:
        return candidates
    return [
        asset
        for asset in candidates
        if fnmatchcase(str(asset["name"]), info.installation.asset_glob)
    ]


def apply_layout(plugin: MarketPlugin, layout: dict) -> dict:
    info = metadata(plugin)
    if not info or not info.installation:
        return layout
    rule = info.installation
    if rule.target_path is None:
        if rule.source_prefix:
            raise PluginPlanError("An explicit source prefix requires an installation target")
        return layout
    source = rule.source_prefix
    entries = layout["entries"]
    if source and not any(
        str(item["path"]) == source or str(item["path"]).startswith(source + "/")
        for item in entries
    ):
        raise PluginPlanError("AI installation source prefix is absent from the release archive")
    return {
        **layout,
        "source_prefix": source or None,
        "mapping": [{"source": source or ".", "target": rule.target_path}],
        "mapping_required": False,
    }


async def managed_market_plugin(market_plugin_id: int | None) -> MarketPlugin | None:
    if market_plugin_id is None:
        return None
    from modules.database import async_session_maker

    async with async_session_maker() as db:
        plugin = await db.get(MarketPlugin, market_plugin_id)
        if plugin is not None:
            validate_installable(plugin)
        return plugin


async def selected_asset_rules(plugin: MarketPlugin, download_url: str) -> dict:
    """Reinspect the actual selected archive before an install or upgrade."""
    from urllib.parse import unquote, urlsplit

    from services.plugins.release_archive import inspect_release_asset_layout

    validate_installable(plugin)
    parsed = urlsplit(download_url)
    expected = plugin.github_url.rstrip("/").removesuffix(".git") + "/releases/download/"
    if (
        not download_url.casefold().startswith(expected.casefold())
        or parsed.query
        or parsed.fragment
    ):
        raise PluginPlanError("AI installation asset must belong to the plugin's GitHub releases")
    asset = {"name": unquote(parsed.path.rsplit("/", 1)[-1]), "url": download_url}
    if not select_assets(plugin, [asset]):
        raise PluginPlanError("Selected release asset does not match the reviewed AI install rule")
    repository = plugin.github_url.rstrip("/").rsplit("/", 1)[-1]
    layout = apply_layout(plugin, await inspect_release_asset_layout(asset, repository))
    if layout["mapping_required"]:
        raise PluginPlanError("Release archive needs an explicit installation mapping")
    mapping = layout["mapping"]
    info = metadata(plugin)
    target = info.installation.target_path if info and info.installation else None
    if target is None and len(mapping) == 1 and mapping[0]["target"] not in {"addons", "cfg"}:
        target = mapping[0]["target"]
    return {
        "archive_sha256": layout["archive_sha256"],
        "source_prefix": layout["source_prefix"],
        "custom_install_path": target,
        "allowed_roots": []
        if target
        else sorted({item["target"].split("/", 1)[0] for item in mapping}),
    }


async def managed_asset_candidates(
    item: ManagedPlugin, assets: list[dict]
) -> tuple[list[dict], str]:
    market = await managed_market_plugin(item.market_plugin_id)
    info = metadata(market) if market else None
    if market and info and info.installation:
        return select_assets(market, assets), info.installation.asset_glob
    return assets, item.asset_glob or "*"


async def managed_install_request(
    item: ManagedPlugin, latest: dict, config_exclusions: list[str]
) -> GitHubPluginInstallRequest:
    request = GitHubPluginInstallRequest(
        download_url=latest["asset"]["browser_download_url"],
        exclude_dirs=item.exclude_dirs or [],
        exclude_files=list(dict.fromkeys((item.exclude_files or []) + config_exclusions)),
        custom_install_path=item.custom_install_path,
        repo_url=item.repo_url,
        release_id=latest["release_id"],
        release_tag=latest["version"],
        asset_name=latest["asset"].get("name"),
        record_installation=False,
        suppress_notification=True,
    )
    market = await managed_market_plugin(item.market_plugin_id)
    if market and metadata(market):
        rules = await selected_asset_rules(market, request.download_url)
        rules["expected_archive_sha256"] = rules.pop("archive_sha256")
        request = GitHubPluginInstallRequest.model_validate({**request.model_dump(), **rules})
    return request
