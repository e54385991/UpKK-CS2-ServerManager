"""Shared dependency, conflict, and market-plugin installation workflows."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from modules import (
    GitHubPluginInstallRequest,
    ManagedPlugin,
    MarketPlugin,
    PluginConflictRule,
    Server,
    User,
)
from modules.http_helper import http_helper
from services.github_credentials import get_effective_github_token
from services.maintenance_lock import maintenance_lock_service
from services.plugin_installation import (
    PLUGIN_INSTALL_MAX_RETRIES,
    _is_retryable_install_failure,
    install_github_plugin,
)
from services.plugin_inventory_service import (
    PluginInventoryError,
    inspect_remote_plugin_inventory,
    installation_evidence,
    verified_market_plugin_ids,
)
from services.plugins.ai_install_policy import (
    apply_layout,
    install_notice,
    metadata,
    select_assets,
    selected_asset_rules,
    validate_installable,
)
from services.plugins.common import PluginPlanError, parse_dependency_ids
from services.plugins.framework_compatibility import (
    evaluate_framework_compatibility,
    framework_mismatch_message,
)
from services.plugins.market_integration import configure_market_plan_handlers
from services.plugins.panel_frameworks import (
    GITHUB_REPOSITORY_PATTERN,
    install_panel_framework,
    panel_framework_key,
)
from services.plugins.progress import emit_plan_progress as _emit_plan_progress
from services.plugins.tracking import derive_asset_glob, upsert_managed_plugin
from services.plugins.upgrade_exclusions import apply_upgrade_mode_exclusions

ProgressCallback = Callable[..., Awaitable[None]]
logger = logging.getLogger(__name__)

_GITHUB_REPOSITORY = GITHUB_REPOSITORY_PATTERN
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
# Framework marketplace entries install through the panel-native installers.
# The aliases keep the names existing callers and tests already patch.
_panel_framework_key = panel_framework_key
_install_panel_framework = install_panel_framework


def _plugin_plan_confirmation_payload(plan: dict[str, Any]) -> dict[str, Any]:
    """Keep approval hashes stable while remote installation evidence changes."""
    return {
        "server_id": plan["server_id"],
        "plugin": plan["plugin"],
        "dependencies": plan["dependencies"],
        "installation_order": plan["installation_order"],
        "hard_conflicts": plan["hard_conflicts"],
        "warnings": plan["warnings"],
        # A runtime mismatch has to invalidate an earlier approval; the rest of
        # the detected runtime state is informational and stays out of the hash.
        "framework_mismatch": bool((plan.get("framework") or {}).get("mismatch")),
        "blocked": plan["blocked"],
        "ai_revisions": plan.get("ai_revisions", {}),
    }


async def _resolve_dependency_order(
    db: AsyncSession, root_plugin_id: int
) -> tuple[list[MarketPlugin], MarketPlugin]:
    cache: dict[int, MarketPlugin] = {}
    visiting: list[int] = []
    visited: set[int] = set()
    ordered: list[MarketPlugin] = []

    async def visit(plugin_id: int) -> None:
        if plugin_id in visiting:
            cycle = visiting[visiting.index(plugin_id) :] + [plugin_id]
            raise PluginPlanError(
                "Plugin dependency cycle detected: " + " -> ".join(map(str, cycle))
            )
        if plugin_id in visited:
            return
        plugin = cache.get(plugin_id) or await MarketPlugin.get_by_id(db, plugin_id)
        if plugin is None:
            raise PluginPlanError(f"Dependency plugin {plugin_id} does not exist")
        validate_installable(plugin)
        cache[plugin_id] = plugin
        visiting.append(plugin_id)
        for dependency_id in parse_dependency_ids(plugin.dependencies):
            if dependency_id == plugin_id:
                raise PluginPlanError(f"Plugin {plugin_id} cannot depend on itself")
            await visit(dependency_id)
        visiting.pop()
        visited.add(plugin_id)
        ordered.append(plugin)

    await visit(root_plugin_id)
    return ordered[:-1], ordered[-1]


async def build_plugin_install_plan(
    db: AsyncSession,
    server_id: int,
    plugin_id: int,
    *,
    include_dependencies: bool = True,
    server: Server | None = None,
) -> dict[str, Any]:
    """Return a deterministic, recursively resolved installation preflight."""
    dependencies, target = await _resolve_dependency_order(db, plugin_id)
    if not include_dependencies:
        dependencies = []
    ordered = [*dependencies, target]
    planned_ids = {int(plugin.id) for plugin in ordered if plugin.id is not None}

    managed_result = await db.execute(
        select(ManagedPlugin).where(ManagedPlugin.server_id == server_id)
    )
    managed = list(managed_result.scalars().all())
    current_server = server or await Server.get_by_id(db, server_id)
    if current_server is None or current_server.id != server_id:
        raise PluginPlanError("Server was not found while verifying installed plugins")
    try:
        inventory = await inspect_remote_plugin_inventory(current_server)
    except PluginInventoryError as exc:
        raise PluginPlanError(f"Unable to verify installed plugins: {exc}") from exc
    installed_ids = verified_market_plugin_ids(managed, ordered, inventory)
    unverified_tracking = sorted(
        item.display_name for item in managed if not installation_evidence(item, inventory)
    )
    matched_remote_keys = {
        evidence["key"]
        for item in [*managed, *ordered]
        for evidence in installation_evidence(item, inventory)
        if evidence.get("key")
    }
    installed_unknown = sorted(
        str(item["name"])
        for item in inventory["plugins"]
        if item.get("key") not in matched_remote_keys
    )

    relevant_ids = planned_ids | installed_ids
    rules: list[PluginConflictRule] = []
    if relevant_ids:
        rule_result = await db.execute(
            select(PluginConflictRule).where(
                col(PluginConflictRule.is_enabled).is_(True),
                or_(
                    col(PluginConflictRule.plugin_a_id).in_(relevant_ids),
                    col(PluginConflictRule.plugin_b_id).in_(relevant_ids),
                ),
            )
        )
        rules = list(rule_result.scalars().all())

    conflicts: list[dict[str, Any]] = []
    for rule in rules:
        left = int(rule.plugin_a_id)
        right = int(rule.plugin_b_id)
        # A rule matters if a newly planned plugin meets another planned or
        # already installed plugin. Existing-existing conflicts do not block an
        # unrelated installation.
        if not (
            (left in planned_ids and right in relevant_ids)
            or (right in planned_ids and left in relevant_ids)
        ):
            continue
        if left == right:
            continue
        conflicts.append(
            {
                "rule_id": rule.id,
                "plugin_a_id": left,
                "plugin_b_id": right,
                "severity": rule.severity,
                "reason": rule.reason or "No reason provided",
            }
        )

    steps = []
    for index, plugin in enumerate(ordered, start=1):
        installed = plugin.id in installed_ids
        has_tracking_record = any(item.market_plugin_id == plugin.id for item in managed)
        steps.append(
            {
                "order": index,
                "plugin_id": plugin.id,
                "title": plugin.title,
                "kind": "target" if plugin.id == target.id else "dependency",
                "status": "already_installed" if installed else "install",
                "reason": (
                    "remote_files_present"
                    if installed
                    else (
                        "tracking_record_without_remote_evidence"
                        if has_tracking_record
                        else "not_found_on_server"
                    )
                ),
            }
        )

    hard_conflicts = [item for item in conflicts if item["severity"] == "hard"]
    warnings = [item for item in conflicts if item["severity"] == "warning"]
    hard_conflicts.sort(key=lambda item: int(item["rule_id"] or 0))
    warnings.sort(key=lambda item: int(item["rule_id"] or 0))
    framework = evaluate_framework_compatibility(
        target.framework, inventory.get("frameworks") or {}
    )
    plan = {
        "server_id": server_id,
        "plugin": {"id": target.id, "title": target.title},
        "dependencies": [
            {"id": dependency.id, "title": dependency.title} for dependency in dependencies
        ],
        "installation_order": [plugin.id for plugin in ordered],
        "already_installed": sorted(installed_ids & planned_ids),
        "tracking_records_without_remote_evidence": unverified_tracking,
        "compatibility_unknown": sorted(installed_unknown),
        "hard_conflicts": hard_conflicts,
        "warnings": warnings,
        "framework": framework,
        "steps": steps,
        "blocked": bool(hard_conflicts),
        "ai_unreviewed": [
            plugin.id for plugin in ordered if (info := metadata(plugin)) and not info.reviewed
        ],
        # Advisory only. Outstanding prerequisites and notes are shown before
        # the install instead of aborting the preflight.
        "ai_notices": [
            notice for plugin in ordered if (notice := install_notice(plugin)) is not None
        ],
        "ai_revisions": {
            str(plugin.id): info.revision()
            for plugin in ordered
            if (info := metadata(plugin)) is not None
        },
    }
    confirmation_payload = _plugin_plan_confirmation_payload(plan)
    encoded = json.dumps(
        confirmation_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    plan["plan_hash"] = hashlib.sha256(encoded.encode()).hexdigest()
    return plan


def validate_plugin_plan_acknowledgements(
    plan: dict[str, Any],
    acknowledged_warning_rule_ids: Iterable[int],
    *,
    acknowledge_framework_mismatch: bool = False,
    acknowledge_ai_unreviewed: bool = False,
) -> None:
    if plan.get("ai_unreviewed") and not acknowledge_ai_unreviewed:
        raise PluginPlanError(
            "AI-generated installation settings require explicit review acknowledgement"
        )
    if plan["hard_conflicts"]:
        ids = ", ".join(str(item["rule_id"]) for item in plan["hard_conflicts"])
        raise PluginPlanError(f"Installation blocked by hard conflict rule(s): {ids}")
    compatibility = plan.get("framework") or {}
    if compatibility.get("mismatch") and not acknowledge_framework_mismatch:
        raise PluginPlanError(
            framework_mismatch_message(compatibility)
            + ". Acknowledge the runtime mismatch to install anyway."
        )
    required = {int(item["rule_id"]) for item in plan["warnings"]}
    acknowledged = {int(item) for item in acknowledged_warning_rule_ids}
    missing = required - acknowledged
    if missing:
        raise PluginPlanError(
            "Explicit acknowledgement required for warning rule(s): "
            + ", ".join(map(str, sorted(missing)))
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
    match = _GITHUB_REPOSITORY.fullmatch(plugin.github_url.strip().rstrip("/"))
    if not match:
        raise PluginPlanError(f"Invalid GitHub URL for {plugin.title}")
    owner, repository = match.groups()
    token = await get_effective_github_token(db, user)
    success, data, error = await http_helper.get(
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
    candidates = select_assets(plugin, _release_asset_candidates(data))
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
            or (mapping[0].get("source", ".") in {".", ""} and mapping[0]["target"] == "addons")
        ):
            inferred_custom_target = mapping[0]["target"]
        info = metadata(plugin)
        custom_target = (
            info.installation.target_path or inferred_custom_target
            if info and info.installation
            else plugin.custom_install_path or inferred_custom_target
        )
        return {
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

    selected_release_id = ""
    selected_release_tag = "unknown"
    selected_asset_name = download_url.rsplit("/", 1)[-1]
    if download_url.startswith("https://github.com/") and "/releases/download/" in download_url:
        release_parts = download_url.split("/releases/download/", 1)[1].split("/", 1)
        if len(release_parts) == 2:
            selected_release_tag = release_parts[0]
            selected_release_id = f"tag:{selected_release_tag}"
            selected_asset_name = release_parts[1]
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


async def _install_one(
    db: AsyncSession,
    plugin: MarketPlugin,
    server: Server,
    user: User,
    progress: ProgressCallback | None = None,
    operation_id: str | None = None,
    linux_runtime_profile: dict[str, Any] | None = None,
    resolved_asset: dict[str, Any] | None = None,
    exclude_dirs: list[str] | None = None,
    exclude_files: list[str] | None = None,
    upgrade_mode: bool = False,
) -> dict[str, Any]:
    framework_key = _panel_framework_key(plugin)
    if framework_key is not None:
        return await _install_panel_framework(
            db,
            plugin,
            server,
            user,
            framework_key,
            progress,
        )

    total_attempts = PLUGIN_INSTALL_MAX_RETRIES + 1
    failures: list[str] = []
    asset: dict[str, Any] | None = None
    result = None
    for attempt in range(1, total_attempts + 1):
        if attempt > 1:
            await _emit_plan_progress(
                progress,
                f"Retrying {plugin.title} installation (attempt {attempt}/{total_attempts})",
                step_id=f"plugin:{plugin.id}",
                step_status="running",
            )
        try:
            asset = (
                resolved_asset
                if attempt == 1 and resolved_asset is not None
                else await _latest_release_asset(
                    db,
                    plugin,
                    server,
                    user,
                    linux_runtime_profile,
                )
            )
            final_exclude_files = list(exclude_files or [])
            if upgrade_mode:
                final_exclude_files = apply_upgrade_mode_exclusions(final_exclude_files)
            request = GitHubPluginInstallRequest(
                download_url=asset["download_url"],
                exclude_dirs=list(exclude_dirs or []),
                exclude_files=final_exclude_files,
                custom_install_path=asset["custom_install_path"],
                record_installation=False,
                suppress_notification=False,
                source_prefix=asset["source_prefix"],
                allowed_roots=asset["allowed_roots"],
                expected_archive_sha256=asset["archive_sha256"],
            )
            result = await install_github_plugin(
                server.id,
                request,
                db,
                user,
                ai_progress=progress,
                operation_id=(f"{operation_id}-market-{plugin.id}" if operation_id else None),
            )
            if result.success:
                break
            failure = result.message
        except LookupError, PermissionError:
            raise
        except Exception as exc:
            failure = str(exc) or exc.__class__.__name__
        failures.append(failure)
        deterministic = failure.casefold().startswith("invalid github url")
        if attempt >= total_attempts or deterministic or not _is_retryable_install_failure(failure):
            break
        await _emit_plan_progress(
            progress,
            f"{plugin.title} installation attempt {attempt}/{total_attempts} failed: "
            f"{failure}. Retrying automatically.",
            step_id=f"plugin:{plugin.id}",
            step_status="running",
        )

    if result is None or not result.success:
        failure = failures[-1] if failures else "Plugin installation failed"
        if len(failures) > 1:
            failure = (
                f"{failure} (installation failed after {len(failures)} attempts, "
                f"including {len(failures) - 1} automatic retries)"
            )
        return {"success": False, "plugin_id": plugin.id, "message": failure}

    assert asset is not None
    tracked_exclude_files = list(exclude_files or [])
    if upgrade_mode:
        tracked_exclude_files = apply_upgrade_mode_exclusions(tracked_exclude_files)
    await upsert_managed_plugin(
        server_id=server.id,
        source_type="market",
        source_key=str(plugin.id),
        display_name=plugin.title,
        repo_url=plugin.github_url,
        market_plugin_id=plugin.id,
        installed_release_id=asset["release_id"],
        installed_version=asset["release_tag"] or plugin.version or "unknown",
        installed_asset_name=asset["asset_name"],
        asset_glob=derive_asset_glob(asset["asset_name"], asset["release_tag"]),
        custom_install_path=asset["custom_install_path"],
        exclude_dirs=list(exclude_dirs or []),
        exclude_files=tracked_exclude_files,
    )
    plugin.download_count += 1
    plugin.install_count += 1
    db.add(plugin)
    await db.commit()
    return {
        "success": True,
        "plugin_id": plugin.id,
        "message": result.message,
        "selected_asset_name": asset["asset_name"],
        "steam_runtime": asset.get("steam_runtime"),
        "linux_runtime_profile": linux_runtime_profile,
        "restart_required": True,
        "next_step": (
            "Restart (or start) the server and wait for startup before locating generated configs."
        ),
    }


@asynccontextmanager
async def _optional_server_lock(
    server_id: int,
    acquire_lock: bool,
    operation: str = "plugin_install_plan",
) -> AsyncIterator[None]:
    if not acquire_lock:
        yield
        return
    async with maintenance_lock_service.get(server_id, operation=operation, wait=False, ttl=1800):
        yield


def _restart_payload(completed: Iterable[dict[str, Any]]) -> dict[str, Any]:
    if not any(bool(item.get("restart_required")) for item in completed):
        return {"restart_required": False}
    return {
        "restart_required": True,
        "next_step": (
            "Restart (or start) the server and wait for startup before searching for, reading, "
            "or patching generated configuration files."
        ),
    }


async def _prepare_plugin_execution(
    db: AsyncSession,
    refreshed_server: Server,
    user: User,
    plugin_id: int,
    acknowledged_warning_rule_ids: Iterable[int],
    expected_plan_hash: str | None,
    include_dependencies: bool,
    download_url: str | None,
    acknowledge_framework_mismatch: bool = False,
    acknowledge_ai_unreviewed: bool = False,
) -> tuple[
    dict[str, Any], set[int], dict[int, MarketPlugin], dict[int, dict[str, Any]], dict | None
]:
    refreshed_plan = await build_plugin_install_plan(
        db,
        refreshed_server.id,
        plugin_id,
        include_dependencies=include_dependencies,
        server=refreshed_server,
    )
    if expected_plan_hash and refreshed_plan["plan_hash"] != expected_plan_hash:
        raise PluginPlanError("Plugin plan changed; review and approve the new plan")
    validate_plugin_plan_acknowledgements(
        refreshed_plan,
        acknowledged_warning_rule_ids,
        acknowledge_framework_mismatch=acknowledge_framework_mismatch,
        acknowledge_ai_unreviewed=acknowledge_ai_unreviewed,
    )
    installed = set(refreshed_plan["already_installed"])
    plugins = await MarketPlugin.get_by_ids(db, refreshed_plan["installation_order"])
    by_id = {plugin.id: plugin for plugin in plugins}
    ordinary_plugin_ids = [
        current_id
        for current_id in refreshed_plan["installation_order"]
        if current_id not in installed
        and (plugin := by_id.get(current_id)) is not None
        and _panel_framework_key(plugin) is None
    ]
    linux_runtime_profile = None
    if ordinary_plugin_ids:
        from services.linux_runtime_service import detect_linux_runtime_profile

        linux_runtime_profile = await detect_linux_runtime_profile(refreshed_server)
    resolved_assets: dict[int, dict[str, Any]] = {}
    for current_id in refreshed_plan["installation_order"]:
        if current_id in installed:
            continue
        plugin = by_id.get(current_id)
        if plugin is None:
            raise PluginPlanError(f"Plugin {current_id} disappeared before execution")
        if current_id == plugin_id and download_url:
            resolved_assets[current_id] = _asset_from_download_url(plugin, download_url)
            if metadata(plugin):
                resolved_assets[current_id].update(await selected_asset_rules(plugin, download_url))
        elif current_id in ordinary_plugin_ids:
            resolved_assets[current_id] = await _latest_release_asset(
                db, plugin, refreshed_server, user, linux_runtime_profile
            )
    return refreshed_plan, installed, by_id, resolved_assets, linux_runtime_profile


async def execute_plugin_install_plan(
    db: AsyncSession,
    server: Server,
    user: User,
    plugin_id: int,
    acknowledged_warning_rule_ids: Iterable[int] = (),
    *,
    expected_plan_hash: str | None = None,
    progress: ProgressCallback | None = None,
    acquire_lock: bool = True,
    lock_operation: str = "plugin_install_plan",
    operation_id: str | None = None,
    include_dependencies: bool = True,
    download_url: str | None = None,
    upgrade_mode: bool = False,
    exclude_dirs: list[str] | None = None,
    exclude_files: list[str] | None = None,
    acknowledge_framework_mismatch: bool = False,
    acknowledge_ai_unreviewed: bool = False,
) -> dict[str, Any]:
    """Recompute and execute a plan, stopping immediately after any failure."""
    plan = await build_plugin_install_plan(
        db,
        server.id,
        plugin_id,
        include_dependencies=include_dependencies,
        server=server,
    )
    if expected_plan_hash and plan["plan_hash"] != expected_plan_hash:
        raise PluginPlanError("Plugin plan changed; review and approve the new plan")
    validate_plugin_plan_acknowledgements(
        plan,
        acknowledged_warning_rule_ids,
        acknowledge_framework_mismatch=acknowledge_framework_mismatch,
        acknowledge_ai_unreviewed=acknowledge_ai_unreviewed,
    )
    completed: list[dict[str, Any]] = []

    async with _optional_server_lock(server.id, acquire_lock, lock_operation):
        # Permission and plan state are intentionally checked again immediately
        # before any remote mutation.
        refreshed_server = (
            await Server.get_by_id(db, server.id)
            if user.is_admin
            else await Server.get_by_id_and_user(db, server.id, user.id)
        )
        if refreshed_server is None:
            raise PluginPlanError("Server permission changed before execution")
        (
            refreshed_plan,
            installed,
            by_id,
            resolved_assets,
            linux_runtime_profile,
        ) = await _prepare_plugin_execution(
            db,
            refreshed_server,
            user,
            plugin_id,
            acknowledged_warning_rule_ids,
            expected_plan_hash,
            include_dependencies,
            download_url,
            acknowledge_framework_mismatch,
            acknowledge_ai_unreviewed,
        )

        for current_id in refreshed_plan["installation_order"]:
            step_id = f"plugin:{current_id}"
            latest_plan = await build_plugin_install_plan(
                db,
                server.id,
                plugin_id,
                include_dependencies=include_dependencies,
                server=refreshed_server,
            )
            validate_plugin_plan_acknowledgements(
                latest_plan,
                acknowledged_warning_rule_ids,
                acknowledge_framework_mismatch=acknowledge_framework_mismatch,
                acknowledge_ai_unreviewed=acknowledge_ai_unreviewed,
            )
            if latest_plan["installation_order"] != refreshed_plan["installation_order"]:
                raise PluginPlanError(
                    "Plugin dependency graph changed during execution; review a new plan"
                )
            if current_id in installed:
                completed.append({"plugin_id": current_id, "success": True, "skipped": True})
                await _emit_plan_progress(
                    progress,
                    f"Plugin {current_id} is already installed",
                    step_id=step_id,
                    step_status="skipped",
                )
                continue
            plugin = by_id.get(current_id)
            if plugin is None:
                raise PluginPlanError(f"Plugin {current_id} disappeared before execution")
            await _emit_plan_progress(
                progress,
                f"Installing {plugin.title}",
                step_id=step_id,
                step_status="running",
            )
            try:
                result = await _install_one(
                    db,
                    plugin,
                    refreshed_server,
                    user,
                    progress,
                    operation_id=operation_id,
                    linux_runtime_profile=linux_runtime_profile,
                    resolved_asset=resolved_assets.get(current_id),
                    exclude_dirs=list(exclude_dirs or []),
                    exclude_files=list(exclude_files or []),
                    upgrade_mode=upgrade_mode,
                )
            except Exception as exc:
                result = {
                    "success": False,
                    "plugin_id": current_id,
                    "message": str(exc),
                }
            completed.append(result)
            if not result["success"]:
                await _emit_plan_progress(
                    progress,
                    str(result.get("message") or f"Failed to install {plugin.title}"),
                    step_id=step_id,
                    step_status="failed",
                )
                completed_ids = {entry["plugin_id"] for entry in completed}
                for remaining_id in refreshed_plan["installation_order"]:
                    if remaining_id in completed_ids:
                        continue
                    await _emit_plan_progress(
                        progress,
                        "Not started because an earlier plugin failed",
                        step_id=f"plugin:{remaining_id}",
                        step_status="interrupted",
                    )
                return {
                    "success": False,
                    "message": f"Stopped after {plugin.title} failed",
                    "completed": completed,
                    "remaining": [
                        item
                        for item in refreshed_plan["installation_order"]
                        if item not in {entry["plugin_id"] for entry in completed}
                    ],
                    **_restart_payload(completed),
                }
            await _emit_plan_progress(
                progress,
                f"Installed {plugin.title}",
                step_id=step_id,
                step_status="completed",
            )

    return {
        "success": True,
        "message": "Plugin installation plan completed",
        "completed": completed,
        "remaining": [],
        **_restart_payload(completed),
    }


# Register the compatibility facade as the implementation behind the leaf
# integration port. GitHub planning can now depend on the port instead of this
# module, so the two workflows no longer form an import cycle.
configure_market_plan_handlers(build_plugin_install_plan, execute_plugin_install_plan)
