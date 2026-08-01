"""Shared dependency, conflict, and market-plugin installation workflows."""

from __future__ import annotations

import hashlib
import json
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
from services.plugin_auto_update_service import derive_asset_glob, upsert_managed_plugin
from services.plugin_installation import install_github_plugin

ProgressCallback = Callable[[str, str], Awaitable[None]]

_GITHUB_REPOSITORY = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_ARCHIVE_EXTENSIONS = (".zip", ".tar.gz", ".tgz", ".tar", ".7z")


class PluginPlanError(ValueError):
    """Raised when a dependency graph or conflict acknowledgement is invalid."""


def parse_dependency_ids(value: str | None) -> list[int]:
    """Parse the legacy comma-separated dependency field without duplicates."""
    if not value:
        return []
    result: list[int] = []
    for item in value.split(","):
        normalized = item.strip()
        if not normalized:
            continue
        if not normalized.isdigit():
            raise PluginPlanError(f"Invalid dependency ID: {normalized}")
        plugin_id = int(normalized)
        if plugin_id not in result:
            result.append(plugin_id)
    return result


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
    installed_ids = {
        int(item.market_plugin_id) for item in managed if item.market_plugin_id is not None
    }
    installed_unknown = [item.display_name for item in managed if item.market_plugin_id is None]

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
        steps.append(
            {
                "order": index,
                "plugin_id": plugin.id,
                "title": plugin.title,
                "kind": "target" if plugin.id == target.id else "dependency",
                "status": "already_installed" if installed else "install",
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
        "compatibility_unknown": sorted(installed_unknown),
        "hard_conflicts": hard_conflicts,
        "warnings": warnings,
        "steps": steps,
        "blocked": bool(hard_conflicts),
    }
    confirmation_payload = {
        key: value for key, value in plan.items() if key != "compatibility_unknown"
    }
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


async def _latest_release_asset(
    db: AsyncSession,
    plugin: MarketPlugin,
    server: Server,
    user: User,
) -> tuple[str, str, str, str]:
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
    for asset in data.get("assets", []):
        name = str(asset.get("name") or "")
        lowered = name.lower()
        if any(marker in lowered for marker in ("windows", "-win-", "_win_")):
            continue
        if lowered.endswith(_ARCHIVE_EXTENSIONS) and asset.get("browser_download_url"):
            return (
                str(asset["browser_download_url"]),
                str(data.get("id") or ""),
                str(data.get("tag_name") or "unknown"),
                name,
            )
    raise PluginPlanError(f"No suitable Linux release asset found for {plugin.title}")


async def _install_one(
    db: AsyncSession,
    plugin: MarketPlugin,
    server: Server,
    user: User,
) -> dict[str, Any]:
    download_url, release_id, release_tag, asset_name = await _latest_release_asset(
        db, plugin, server, user
    )
    request = GitHubPluginInstallRequest(
        download_url=download_url,
        custom_install_path=plugin.custom_install_path,
        record_installation=False,
        suppress_notification=False,
    )
    result = await install_github_plugin(server.id, request, db, user)
    if not result.success:
        return {"success": False, "plugin_id": plugin.id, "message": result.message}

    await upsert_managed_plugin(
        server_id=server.id,
        source_type="market",
        source_key=str(plugin.id),
        display_name=plugin.title,
        repo_url=plugin.github_url,
        market_plugin_id=plugin.id,
        installed_release_id=release_id,
        installed_version=release_tag or plugin.version or "unknown",
        asset_glob=derive_asset_glob(asset_name, release_tag),
        custom_install_path=plugin.custom_install_path,
    )
    plugin.download_count += 1
    plugin.install_count += 1
    db.add(plugin)
    await db.commit()
    return {"success": True, "plugin_id": plugin.id, "message": result.message}


@asynccontextmanager
async def _optional_server_lock(server_id: int, acquire_lock: bool) -> AsyncIterator[None]:
    if not acquire_lock:
        yield
        return
    async with maintenance_lock_service.get(
        server_id, operation="plugin_install_plan", wait=False, ttl=1800
    ):
        yield


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
) -> dict[str, Any]:
    """Recompute and execute a plan, stopping immediately after any failure."""
    plan = await build_plugin_install_plan(db, server.id, plugin_id)
    if expected_plan_hash and plan["plan_hash"] != expected_plan_hash:
        raise PluginPlanError("Plugin plan changed; review and approve the new plan")
    validate_plugin_plan_acknowledgements(plan, acknowledged_warning_rule_ids)
    completed: list[dict[str, Any]] = []

    async with _optional_server_lock(server.id, acquire_lock):
        # Permission and plan state are intentionally checked again immediately
        # before any remote mutation.
        refreshed_server = (
            await Server.get_by_id(db, server.id)
            if user.is_admin
            else await Server.get_by_id_and_user(db, server.id, user.id)
        )
        if refreshed_server is None:
            raise PluginPlanError("Server permission changed before execution")
        refreshed_plan = await build_plugin_install_plan(db, server.id, plugin_id)
        if expected_plan_hash and refreshed_plan["plan_hash"] != expected_plan_hash:
            raise PluginPlanError("Plugin plan changed; review and approve the new plan")
        validate_plugin_plan_acknowledgements(refreshed_plan, acknowledged_warning_rule_ids)
        installed = set(refreshed_plan["already_installed"])
        plugins = await MarketPlugin.get_by_ids(db, refreshed_plan["installation_order"])
        by_id = {plugin.id: plugin for plugin in plugins}

        for current_id in refreshed_plan["installation_order"]:
            latest_plan = await build_plugin_install_plan(db, server.id, plugin_id)
            validate_plugin_plan_acknowledgements(latest_plan, acknowledged_warning_rule_ids)
            if latest_plan["installation_order"] != refreshed_plan["installation_order"]:
                raise PluginPlanError(
                    "Plugin dependency graph changed during execution; review a new plan"
                )
            if current_id in installed:
                completed.append({"plugin_id": current_id, "success": True, "skipped": True})
                continue
            plugin = by_id.get(current_id)
            if plugin is None:
                raise PluginPlanError(f"Plugin {current_id} disappeared before execution")
            if progress:
                await progress(f"Installing {plugin.title}", "status")
            try:
                result = await _install_one(db, plugin, refreshed_server, user)
            except Exception as exc:
                result = {
                    "success": False,
                    "plugin_id": current_id,
                    "message": str(exc),
                }
            completed.append(result)
            if not result["success"]:
                return {
                    "success": False,
                    "message": f"Stopped after {plugin.title} failed",
                    "completed": completed,
                    "remaining": [
                        item
                        for item in refreshed_plan["installation_order"]
                        if item not in {entry["plugin_id"] for entry in completed}
                    ],
                }

    return {
        "success": True,
        "message": "Plugin installation plan completed",
        "completed": completed,
        "remaining": [],
    }
