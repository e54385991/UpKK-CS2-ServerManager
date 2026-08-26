"""Shared dependency, conflict, and market-plugin installation workflows."""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

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
from services.plugins.common import PluginPlanError, parse_dependency_ids
from services.plugins.market_integration import configure_market_plan_handlers
from services.plugins.tracking import derive_asset_glob, upsert_managed_plugin

ProgressCallback = Callable[..., Awaitable[None]]
logger = logging.getLogger(__name__)

_GITHUB_REPOSITORY = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
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
_PANEL_FRAMEWORK_REPOSITORIES = {
    ("alliedmodders", "metamod-source"): "metamod",
    ("roflmuffin", "counterstrikesharp"): "counterstrikesharp",
}


def _panel_framework_key(plugin: MarketPlugin) -> str | None:
    """Identify frameworks that must use the panel's dedicated installers."""
    match = _GITHUB_REPOSITORY.fullmatch((plugin.github_url or "").strip().rstrip("/"))
    if match:
        repository_key = tuple(part.casefold() for part in match.groups())
        if repository_key in _PANEL_FRAMEWORK_REPOSITORIES:
            return _PANEL_FRAMEWORK_REPOSITORIES[repository_key]
    normalized_title = re.sub(r"[^a-z0-9]+", "", (plugin.title or "").casefold())
    if normalized_title in {"metamod", "metamodsource"}:
        return "metamod"
    if normalized_title == "counterstrikesharp":
        return "counterstrikesharp"
    return None


async def _install_panel_framework(
    db: AsyncSession,
    plugin: MarketPlugin,
    server: Server,
    user: User,
    framework_key: str,
    progress: ProgressCallback | None,
) -> dict[str, Any]:
    """Route framework market entries through the panel-native installer."""
    from services.plugin_auto_update_service import record_framework_installation
    from services.ssh_manager import SSHManager

    await _emit_plan_progress(
        progress,
        f"Using the panel-native installer for {plugin.title}",
        step_id=f"plugin:{plugin.id}",
        step_status="running",
    )

    async def framework_progress(message: str) -> None:
        await _emit_plan_progress(
            progress,
            message,
            step_id=f"plugin:{plugin.id}",
            step_status="running",
        )

    manager = SSHManager()
    if framework_key == "metamod":
        success, message = await manager.install_metamod(server, framework_progress)
        installed_frameworks = ("metamod",)
    else:
        success, message = await manager.install_counterstrikesharp(server, framework_progress)
        installed_frameworks = ("metamod", "counterstrikesharp")
    if not success:
        return {"success": False, "plugin_id": plugin.id, "message": message}

    tracking_failed = False
    for installed_framework in installed_frameworks:
        try:
            await record_framework_installation(server, user, installed_framework)
        except Exception as exc:
            tracking_failed = True
            logger.warning(
                "Framework %s installed on server %s but tracking refresh failed: %s",
                installed_framework,
                server.id,
                exc,
            )
    plugin.download_count += 1
    plugin.install_count += 1
    db.add(plugin)
    await db.commit()
    result: dict[str, Any] = {
        "success": True,
        "plugin_id": plugin.id,
        "message": message,
        "installation_method": "panel_native",
        "framework": framework_key,
        "restart_required": True,
        "next_step": (
            "Restart (or start) the server and wait for startup before locating generated configs."
        ),
    }
    if tracking_failed:
        result["tracking_warning"] = (
            "The framework was installed, but panel version tracking could not be refreshed."
        )
    return result


async def _emit_plan_progress(
    progress: ProgressCallback | None,
    message: str,
    *,
    step_id: str,
    step_status: str,
) -> None:
    if progress is None:
        return
    metadata = {"step_id": step_id, "step_status": step_status}
    try:
        parameters = inspect.signature(progress).parameters.values()
        accepts_metadata = any(item.kind == inspect.Parameter.VAR_POSITIONAL for item in parameters)
        accepts_metadata = (
            accepts_metadata
            or sum(
                item.kind
                in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                for item in parameters
            )
            >= 3
        )
    except TypeError, ValueError:
        accepts_metadata = False
    if accepts_metadata:
        await progress(message, "status", metadata)
    else:
        await progress(message, "status")


def _plugin_plan_confirmation_payload(plan: dict[str, Any]) -> dict[str, Any]:
    """Keep approval hashes stable while remote installation evidence changes."""
    return {
        "server_id": plan["server_id"],
        "plugin": plan["plugin"],
        "dependencies": plan["dependencies"],
        "installation_order": plan["installation_order"],
        "hard_conflicts": plan["hard_conflicts"],
        "warnings": plan["warnings"],
        "blocked": plan["blocked"],
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
                PluginConflictRule.is_enabled.is_(True),
                or_(
                    PluginConflictRule.plugin_a_id.in_(relevant_ids),
                    PluginConflictRule.plugin_b_id.in_(relevant_ids),
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
        "steps": steps,
        "blocked": bool(hard_conflicts),
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
    plan: dict[str, Any], acknowledged_warning_rule_ids: Iterable[int]
) -> None:
    if plan["hard_conflicts"]:
        ids = ", ".join(str(item["rule_id"]) for item in plan["hard_conflicts"])
        raise PluginPlanError(f"Installation blocked by hard conflict rule(s): {ids}")
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
    candidates = _release_asset_candidates(data)
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
            layout = await inspect_release_asset_layout(asset, repository)
        except GitHubPlanError as exc:
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
        custom_target = plugin.custom_install_path or inferred_custom_target
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


async def _install_one(
    db: AsyncSession,
    plugin: MarketPlugin,
    server: Server,
    user: User,
    progress: ProgressCallback | None = None,
    operation_id: str | None = None,
    linux_runtime_profile: dict[str, Any] | None = None,
    resolved_asset: dict[str, Any] | None = None,
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
            request = GitHubPluginInstallRequest(
                download_url=asset["download_url"],
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
) -> dict[str, Any]:
    """Recompute and execute a plan, stopping immediately after any failure."""
    plan = await build_plugin_install_plan(db, server.id, plugin_id, server=server)
    if expected_plan_hash and plan["plan_hash"] != expected_plan_hash:
        raise PluginPlanError("Plugin plan changed; review and approve the new plan")
    validate_plugin_plan_acknowledgements(plan, acknowledged_warning_rule_ids)
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
        refreshed_plan = await build_plugin_install_plan(
            db, server.id, plugin_id, server=refreshed_server
        )
        if expected_plan_hash and refreshed_plan["plan_hash"] != expected_plan_hash:
            raise PluginPlanError("Plugin plan changed; review and approve the new plan")
        validate_plugin_plan_acknowledgements(refreshed_plan, acknowledged_warning_rule_ids)
        installed = set(refreshed_plan["already_installed"])
        plugins = await MarketPlugin.get_by_ids(db, refreshed_plan["installation_order"])
        by_id = {plugin.id: plugin for plugin in plugins}

        resolved_assets: dict[int, dict[str, Any]] = {}
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
        # Resolve every ordinary asset before installing the first dependency.
        # This guarantees an unknown RT3/RT4 choice cannot leave partial changes.
        for current_id in refreshed_plan["installation_order"]:
            if current_id in installed:
                continue
            plugin = by_id.get(current_id)
            if plugin is None:
                raise PluginPlanError(f"Plugin {current_id} disappeared before execution")
            if current_id in ordinary_plugin_ids:
                resolved_assets[current_id] = await _latest_release_asset(
                    db,
                    plugin,
                    refreshed_server,
                    user,
                    linux_runtime_profile,
                )

        for current_id in refreshed_plan["installation_order"]:
            step_id = f"plugin:{current_id}"
            latest_plan = await build_plugin_install_plan(
                db, server.id, plugin_id, server=refreshed_server
            )
            validate_plugin_plan_acknowledgements(latest_plan, acknowledged_warning_rule_ids)
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
