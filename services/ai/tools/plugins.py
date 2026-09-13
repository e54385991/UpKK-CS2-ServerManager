"""Marketplace, diagnostic, and managed-plugin AI tools."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from modules.models import ManagedPlugin, PluginCategory, Server, User
from modules.schemas.discord import AgentCapability
from services.ai.tools.context import (
    ToolContext,
    canonical_arguments,
    tools,
)
from services.ai.tools.schemas import (
    ApplyManagedPluginUpgradeInput,
    ApplyPluginPlanInput,
    DiagnosticExecuteInput,
    DiagnosticPlanInput,
    DiagnosticRunInput,
    EmptyInput,
    KnowledgeInput,
    ManagedPluginUpgradeInput,
    PluginPlanInput,
    PluginSearchInput,
)


async def _market_release_selection_preview(
    db: AsyncSession,
    server: Server,
    user: User,
    plan: dict[str, Any],
    linux_runtime_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    """Resolve every pending market asset for the AI approval preview."""
    from services.linux_runtime_service import steam_runtime_for_asset
    from services.plugin_conflict_service import (
        _latest_release_asset,
        _panel_framework_key,
    )

    pending_ids = [
        plugin_id
        for plugin_id in plan["installation_order"]
        if plugin_id not in set(plan["already_installed"])
    ]
    plugins = await tools.MarketPlugin.get_by_ids(db, pending_ids)
    by_id = {plugin.id: plugin for plugin in plugins}
    selections: list[dict[str, Any]] = []
    for plugin_id in pending_ids:
        plugin = by_id.get(plugin_id)
        if plugin is None:
            raise ValueError(f"Plugin {plugin_id} disappeared while resolving release assets")
        framework_key = _panel_framework_key(plugin)
        if framework_key is not None:
            selections.append(
                {
                    "plugin_id": plugin_id,
                    "title": plugin.title,
                    "installation_method": "panel_native",
                    "framework": framework_key,
                }
            )
            continue
        asset = await _latest_release_asset(
            db,
            plugin,
            server,
            user,
            linux_runtime_profile,
        )
        runtime = steam_runtime_for_asset(asset["asset_name"])
        selections.append(
            {
                "plugin_id": plugin_id,
                "title": plugin.title,
                "release_id": asset["release_id"],
                "release_tag": asset["release_tag"],
                "asset_name": asset["asset_name"],
                "steam_runtime": runtime,
                "selection_reason": (
                    linux_runtime_profile["reason"]
                    if runtime
                    else "The release does not contain a paired SteamRT3/SteamRT4 asset family"
                ),
            }
        )
    return selections


async def lookup_cs2_knowledge(ctx: ToolContext, data: KnowledgeInput) -> dict[str, Any]:
    del ctx
    return {"topic": data.topic, "content": tools.lookup_knowledge(data.topic)}


async def search_plugin_market(ctx: ToolContext, data: PluginSearchInput) -> dict[str, Any]:
    try:
        category = PluginCategory(data.category) if data.category else None
    except ValueError as exc:
        raise ValueError("Unknown plugin category") from exc
    plugins, total = await tools.MarketPlugin.search_plugins(
        ctx.db,
        category=category,
        search_query=data.query,
        limit=data.limit,
    )
    return {
        "total": total,
        "plugins": [
            {
                "id": plugin.id,
                "title": plugin.title,
                "version": plugin.version,
                "category": plugin.category.value,
                "description": (plugin.description or "")[:500],
                "dependencies": plugin.dependencies,
            }
            for plugin in plugins
        ],
    }


async def list_installed_plugins(ctx: ToolContext, _: EmptyInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    result = await ctx.db.execute(select(ManagedPlugin).where(ManagedPlugin.server_id == server.id))
    tracked = list(result.scalars().all())
    try:
        inventory = await tools.inspect_remote_plugin_inventory(server)
        remote_inspection: dict[str, Any] = {
            "status": "success",
            **inventory,
            "note": "Filesystem presence is verified; runtime loading is not verified.",
        }
    except tools.PluginInventoryError as exc:
        inventory = {"frameworks": {}, "plugins": [], "truncated": False}
        remote_inspection = {
            "status": "unavailable",
            "error": str(exc),
            "frameworks": {},
            "plugins": [],
            "truncated": False,
        }
    return {
        "remote_inspection": remote_inspection,
        "tracking_records": [
            {
                "id": plugin.id,
                "name": plugin.display_name,
                "source_type": plugin.source_type,
                "market_plugin_id": plugin.market_plugin_id,
                "framework": plugin.framework_key,
                "recorded_version": plugin.installed_version,
                "remote_status": (
                    "files_present"
                    if tools.installation_evidence(plugin, inventory)
                    else (
                        "not_found_by_inventory"
                        if remote_inspection["status"] == "success"
                        else "unknown"
                    )
                ),
                "remote_evidence": tools.installation_evidence(plugin, inventory),
            }
            for plugin in tracked
        ],
        "warning": (
            "tracking_records are panel metadata, not proof of installation. "
            "Only remote_inspection contains current filesystem evidence, and recorded_version "
            "is not a remotely verified version."
        ),
    }


async def plan_plugin_install(ctx: ToolContext, data: PluginPlanInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    from services.plugin_conflict_service import _panel_framework_key, build_plugin_install_plan

    plugin = await tools.MarketPlugin.get_by_id(ctx.db, data.plugin_id)
    framework_key = _panel_framework_key(plugin) if plugin is not None else None
    if plugin is not None and framework_key is not None:
        operation = (
            "install_metamod" if framework_key == "metamod" else "install_counterstrikesharp"
        )
        raise ValueError(
            f"{plugin.title} is a panel-managed framework. Use run_server_operation with "
            f"operation={operation}, not a plugin-market plan."
        )

    plan = await build_plugin_install_plan(ctx.db, server.id, data.plugin_id, server=server)
    from services.linux_runtime_service import detect_linux_runtime_profile

    linux_runtime_profile = await detect_linux_runtime_profile(server)
    plan["linux_runtime_profile"] = linux_runtime_profile
    plan["release_selections"] = await tools._market_release_selection_preview(
        ctx.db,
        server,
        ctx.user,
        plan,
        linux_runtime_profile,
    )
    return plan


async def plan_plugin_crash_isolation(
    ctx: ToolContext, data: DiagnosticPlanInput
) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    from services.plugin_diagnostic_service import build_diagnostic_plan

    return await build_diagnostic_plan(ctx.db, ctx.user, server.id, data.scope)


async def execute_plugin_crash_isolation(
    ctx: ToolContext, data: DiagnosticExecuteInput
) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    from services.plugin_diagnostic_service import execute_diagnostic_plan

    return await execute_diagnostic_plan(
        ctx.db,
        ctx.user,
        server.id,
        data.scope,
        data.expected_plan_hash,
        ai_run_id=ctx.run_id,
        progress=lambda event_type, payload: ctx.emit(event_type, payload),
    )


async def get_plugin_crash_isolation(ctx: ToolContext, data: DiagnosticRunInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    from services.plugin_diagnostic_service import get_diagnostic_run

    return await get_diagnostic_run(ctx.db, ctx.user, server.id, data.diagnostic_id)


async def restore_plugin_quarantine(ctx: ToolContext, data: DiagnosticRunInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    from services.plugin_diagnostic_service import restore_diagnostic_run

    return await restore_diagnostic_run(ctx.db, ctx.user, server.id, data.diagnostic_id)


async def apply_plugin_plan(ctx: ToolContext, data: ApplyPluginPlanInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    from services.plugin_conflict_service import (
        build_plugin_install_plan,
        execute_plugin_install_plan,
    )

    if ctx.enforce_agent_policy:
        plan = await build_plugin_install_plan(ctx.db, server.id, data.plugin_id, server=server)
        plugins = await tools.MarketPlugin.get_by_ids(ctx.db, plan["installation_order"])
        from services.plugin_conflict_service import _panel_framework_key

        if any(
            _panel_framework_key(plugin) is not None
            and plugin.id not in set(plan["already_installed"])
            for plugin in plugins
        ):
            from services.agent_policy_service import require_agent_capabilities

            await require_agent_capabilities(
                ctx.db, server.id, frozenset({AgentCapability.MANAGE_FRAMEWORKS})
            )

    return await execute_plugin_install_plan(
        ctx.db,
        server,
        ctx.user,
        data.plugin_id,
        set(data.acknowledge_warning_rule_ids),
        expected_plan_hash=data.expected_plan_hash,
        progress=lambda message, message_type, metadata=None: ctx.emit(
            "tool_progress",
            {"message": message, "message_type": message_type, **(metadata or {})},
        ),
        lock_operation="ai:plugin_install_plan",
        operation_id=ctx.run_id,
    )


async def plan_managed_plugin_upgrade(
    ctx: ToolContext, data: ManagedPluginUpgradeInput
) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    from services.plugin_auto_update_service import plugin_auto_update_service

    plan = await plugin_auto_update_service.build_plugin_upgrade_plan(server.id, data.plugin_id)
    _, plan_hash = canonical_arguments(plan)
    return {**plan, "plan_hash": plan_hash}


async def apply_managed_plugin_upgrade(
    ctx: ToolContext, data: ApplyManagedPluginUpgradeInput
) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    from services.plugin_auto_update_service import plugin_auto_update_service

    plan = await plugin_auto_update_service.build_plugin_upgrade_plan(server.id, data.plugin_id)
    _, plan_hash = canonical_arguments(plan)
    if plan_hash != data.expected_plan_hash:
        raise PermissionError("Managed plugin upgrade plan changed after approval")
    if plan["no_op"]:
        return {"success": True, "message": "No plugin update available", "no_op": True}
    from services.operation_enqueue import enqueue_plugin_auto_update
    from services.server_operation_hub import server_operation_hub

    record = await enqueue_plugin_auto_update(
        server_id=server.id,
        actor_user_id=ctx.user.id,
        plugin_id=data.plugin_id,
        force=True,
    )
    final = await server_operation_hub.wait_until_terminal(str(record["operation_id"]))
    return {
        "success": bool(final.get("success")),
        "message": str(final.get("message") or ""),
        "no_op": False,
    }
