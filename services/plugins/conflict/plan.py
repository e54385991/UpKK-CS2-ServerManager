"""Build and validate market plugin install plans."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from modules import ManagedPlugin, MarketPlugin, PluginConflictRule, Server
from services.compat import LateBoundModule
from services.plugin_inventory_service import PluginInventoryError
from services.plugins.ai_install_policy import install_notice, metadata, validate_installable
from services.plugins.common import PluginPlanError
from services.plugins.framework_compatibility import (
    evaluate_framework_compatibility,
    framework_mismatch_message,
)

logger = logging.getLogger(__name__)
host = LateBoundModule("services.plugin_conflict_service")


class PreflightState:
    __slots__ = (
        "server_id",
        "server",
        "target",
        "dependencies",
        "ordered",
        "managed",
        "planned_ids",
    )

    def __init__(
        self,
        server_id: int,
        server: Server,
        target: MarketPlugin,
        dependencies: list[MarketPlugin],
        ordered: list[MarketPlugin],
        managed: list[ManagedPlugin],
    ) -> None:
        self.server_id = server_id
        self.server = server
        self.target = target
        self.dependencies = dependencies
        self.ordered = ordered
        self.managed = managed
        self.planned_ids = {int(plugin.id) for plugin in ordered if plugin.id is not None}


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


def _session_supports_batch(db: object) -> bool:
    return getattr(db, "bind", None) is not None or getattr(db, "get_bind", None) is not None


async def _fill_plugin_cache(
    db: AsyncSession, plugin_ids: list[int], cache: dict[int, MarketPlugin]
) -> None:
    missing = [plugin_id for plugin_id in dict.fromkeys(plugin_ids) if plugin_id not in cache]
    if not missing:
        return
    if _session_supports_batch(db):
        loaded = await host.MarketPlugin.get_by_ids(db, missing)
        for plugin in loaded:
            if plugin.id is not None:
                cache[int(plugin.id)] = plugin
    still_missing = [plugin_id for plugin_id in missing if plugin_id not in cache]
    for plugin_id in still_missing:
        plugin = await host.MarketPlugin.get_by_id(db, plugin_id)
        if plugin is not None:
            cache[plugin_id] = plugin


async def _prefetch_dependency_graph(
    db: AsyncSession, root_plugin_id: int
) -> dict[int, MarketPlugin]:
    cache: dict[int, MarketPlugin] = {}
    pending = [root_plugin_id]
    queued = {root_plugin_id}
    while pending:
        batch = pending
        pending = []
        await _fill_plugin_cache(db, batch, cache)
        for plugin_id in batch:
            plugin = cache.get(plugin_id)
            if plugin is None:
                continue
            for dependency_id in host.parse_dependency_ids(plugin.dependencies):
                if dependency_id not in queued:
                    queued.add(dependency_id)
                    pending.append(dependency_id)
    return cache


async def _resolve_dependency_order(
    db: AsyncSession, root_plugin_id: int
) -> tuple[list[MarketPlugin], MarketPlugin]:
    cache = await _prefetch_dependency_graph(db, root_plugin_id)
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
        plugin = cache.get(plugin_id) or await host.MarketPlugin.get_by_id(db, plugin_id)
        if plugin is None:
            raise PluginPlanError(f"Dependency plugin {plugin_id} does not exist")
        validate_installable(plugin)
        cache[plugin_id] = plugin
        visiting.append(plugin_id)
        for dependency_id in host.parse_dependency_ids(plugin.dependencies):
            if dependency_id == plugin_id:
                raise PluginPlanError(f"Plugin {plugin_id} cannot depend on itself")
            await visit(dependency_id)
        visiting.pop()
        visited.add(plugin_id)
        ordered.append(plugin)

    await visit(root_plugin_id)
    return ordered[:-1], ordered[-1]


async def _read_preflight_state(
    db: AsyncSession,
    server_id: int,
    plugin_id: int,
    *,
    include_dependencies: bool,
    server: Server | None,
) -> PreflightState:
    dependencies, target = await host._resolve_dependency_order(db, plugin_id)
    if not include_dependencies:
        dependencies = []
    ordered = [*dependencies, target]
    managed_result = await db.execute(
        select(ManagedPlugin).where(ManagedPlugin.server_id == server_id)
    )
    current_server = server or await host.Server.get_by_id(db, server_id)
    if current_server is None or current_server.id != server_id:
        raise PluginPlanError("Server was not found while verifying installed plugins")
    state = PreflightState(
        server_id,
        current_server,
        target,
        dependencies,
        ordered,
        list(managed_result.scalars().all()),
    )
    _materialize_preflight_state(state)
    return state


def _materialize_preflight_state(state: PreflightState) -> None:
    """Load column values before the short read session closes."""
    server = state.server
    _ = (
        server.id,
        getattr(server, "host", None),
        getattr(server, "ssh_port", None),
        getattr(server, "ssh_user", None),
        getattr(server, "auth_type", None),
        getattr(server, "ssh_password", None),
        getattr(server, "ssh_key_path", None),
        getattr(server, "sudo_password", None),
        getattr(server, "game_directory", None),
        getattr(server, "github_proxy", None),
        getattr(server, "is_password_auth", None),
        getattr(server, "is_key_auth", None),
    )
    for plugin in state.ordered:
        _ = (
            plugin.id,
            plugin.title,
            plugin.framework,
            plugin.dependencies,
            plugin.github_url,
            plugin.custom_install_path,
            plugin.ai_metadata,
        )
    for item in state.managed:
        _ = (
            item.display_name,
            item.market_plugin_id,
            getattr(item, "framework_key", None),
            getattr(item, "repo_url", None),
            getattr(item, "custom_install_path", None),
        )


async def _inspect_inventory(server: Server) -> dict[str, Any]:
    try:
        return await host.inspect_remote_plugin_inventory(server)
    except PluginInventoryError as exc:
        raise PluginPlanError(f"Unable to verify installed plugins: {exc}") from exc


async def _load_conflict_rules(
    db: AsyncSession, relevant_ids: set[int]
) -> list[PluginConflictRule]:
    if not relevant_ids:
        return []
    rule_result = await db.execute(
        select(PluginConflictRule).where(
            col(PluginConflictRule.is_enabled).is_(True),
            or_(
                col(PluginConflictRule.plugin_a_id).in_(relevant_ids),
                col(PluginConflictRule.plugin_b_id).in_(relevant_ids),
            ),
        )
    )
    return list(rule_result.scalars().all())


async def build_plugin_install_plan(
    db: AsyncSession,
    server_id: int,
    plugin_id: int,
    *,
    include_dependencies: bool = True,
    server: Server | None = None,
) -> dict[str, Any]:
    """Compatibility entry: uses the caller's session and never commits it."""
    state = await _read_preflight_state(
        db,
        server_id,
        plugin_id,
        include_dependencies=include_dependencies,
        server=server,
    )
    inventory = await _inspect_inventory(state.server)
    installed_ids = host.verified_market_plugin_ids(state.managed, state.ordered, inventory)
    rules = await _load_conflict_rules(db, state.planned_ids | installed_ids)
    return _assemble_plan(state, inventory, installed_ids, rules)


async def plan_plugin_install(
    server_id: int,
    plugin_id: int,
    *,
    include_dependencies: bool = True,
    server: Server | None = None,
    session_factory: Any | None = None,
) -> dict[str, Any]:
    """Own short read transactions; SSH runs only after the first session closes."""
    from modules.database import async_session_maker

    factory = session_factory or async_session_maker
    async with factory() as db:
        state = await _read_preflight_state(
            db,
            server_id,
            plugin_id,
            include_dependencies=include_dependencies,
            server=server,
        )
    inventory = await _inspect_inventory(state.server)
    installed_ids = host.verified_market_plugin_ids(state.managed, state.ordered, inventory)
    async with factory() as db:
        rules = await _load_conflict_rules(db, state.planned_ids | installed_ids)
        return _assemble_plan(state, inventory, installed_ids, rules)


def _assemble_plan(
    state: PreflightState,
    inventory: dict[str, Any],
    installed_ids: set[int],
    rules: list[PluginConflictRule],
) -> dict[str, Any]:
    planned_ids = state.planned_ids
    relevant_ids = planned_ids | installed_ids
    target = state.target
    ordered = state.ordered
    dependencies = state.dependencies
    managed = state.managed
    unverified_tracking = sorted(
        item.display_name for item in managed if not host.installation_evidence(item, inventory)
    )
    matched_remote_keys = {
        evidence["key"]
        for item in [*managed, *ordered]
        for evidence in host.installation_evidence(item, inventory)
        if evidence.get("key")
    }
    installed_unknown = sorted(
        str(item["name"])
        for item in inventory["plugins"]
        if item.get("key") not in matched_remote_keys
    )
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
        "server_id": state.server_id,
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
    # Unreviewed AI metadata remains an explicit operator decision, but it no
    # longer rejects the plan before the UI can show its advisory details.
    if plan.get("ai_unreviewed") and not acknowledge_ai_unreviewed:
        logger.warning(
            "Installing AI-collected plugin(s) %s without reviewed metadata",
            ", ".join(map(str, plan["ai_unreviewed"])),
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
