"""Read-only approval cards for mutating AI tools."""

from __future__ import annotations

from typing import Any

from modules.models import Server
from modules.schemas.discord import AgentCapability
from services.ai.tools.context import (
    ToolContext,
    canonical_arguments,
    tools,
)
from services.ai.tools.schemas import (
    ApplyManagedPluginUpgradeInput,
    ApplyPluginPlanInput,
    ApplyServerStartupPlanInput,
    ApplyWorkshopPlanInput,
    GitHubApplyInput,
)


async def _apply_plugin_plan_summary(
    arguments: dict[str, Any], context: ToolContext, server: Server, base: dict[str, Any]
) -> dict[str, Any]:
    data = ApplyPluginPlanInput.model_validate(arguments)
    from services.plugin_conflict_service import build_plugin_install_plan

    plan = await build_plugin_install_plan(context.db, server.id, data.plugin_id, server=server)
    if plan["plan_hash"] != data.expected_plan_hash:
        raise ValueError("Plugin plan changed before approval")
    plugins = await tools.MarketPlugin.get_by_ids(context.db, plan["installation_order"])
    from services.plugin_conflict_service import _panel_framework_key

    if any(
        _panel_framework_key(plugin) is not None and plugin.id not in set(plan["already_installed"])
        for plugin in plugins
    ):
        from services.agent_policy_service import require_agent_capabilities

        await require_agent_capabilities(
            context.db, server.id, frozenset({AgentCapability.MANAGE_FRAMEWORKS})
        )
    from services.linux_runtime_service import detect_linux_runtime_profile

    linux_runtime_profile = await detect_linux_runtime_profile(server)
    release_selections = await tools._market_release_selection_preview(
        context.db,
        server,
        context.user,
        plan,
        linux_runtime_profile,
    )
    return {
        **base,
        "target": plan["plugin"],
        "steps": plan["steps"],
        "hard_conflicts": plan["hard_conflicts"],
        "warnings": plan["warnings"],
        "linux_runtime_profile": linux_runtime_profile,
        "release_selections": release_selections,
        "post_install": (
            "A separate server restart/start is required before generated configs are inspected"
        ),
        "expected_result": (
            "Dependencies install in order; execution stops at the first failure and reports "
            "restart_required after any successful installation"
        ),
    }


async def _apply_workshop_map_summary(
    arguments: dict[str, Any], context: ToolContext, server: Server, base: dict[str, Any]
) -> dict[str, Any]:
    data = ApplyWorkshopPlanInput.model_validate(arguments)
    from services.workshop_map_service import build_workshop_map_plan

    plan = await build_workshop_map_plan(
        context.db,
        server,
        data.model_dump(exclude={"acknowledge_warning_rule_ids", "expected_plan_hash"}),
    )
    if plan["plan_hash"] != data.expected_plan_hash:
        raise ValueError("Workshop plan changed before approval")
    return {
        **base,
        "target": plan["workshop"],
        "steps": plan["steps"],
        "warnings": plan["warnings"],
        "expected_result": plan["download_behavior"],
    }


async def _apply_github_plugin_install_summary(
    arguments: dict[str, Any], context: ToolContext, server: Server, base: dict[str, Any]
) -> dict[str, Any]:
    data = GitHubApplyInput.model_validate(arguments)
    from modules.schemas.plugins import GitHubPluginInstallPlanRequest
    from services.github_plugin_plan_service import build_github_install_plan

    request = GitHubPluginInstallPlanRequest.model_validate(
        data.model_dump(
            exclude={
                "expected_plan_hash",
                "acknowledge_warning_rule_ids",
                "acknowledge_unknown_compatibility",
            }
        )
    )
    plan = await build_github_install_plan(context.db, context.user, server.id, request)
    if plan["plan_hash"] != data.expected_plan_hash:
        raise ValueError("GitHub installation plan changed before approval")
    return {
        **base,
        "repository": plan["repo_url"],
        "release": plan["release_tag"],
        "asset": plan["asset"],
        "archive_sha256": plan["archive_sha256"],
        "mapping": plan["mapping"],
        "config_policy": plan["config_policy"],
        "warnings": plan["warnings"],
        "hard_conflicts": plan["hard_conflicts"],
        "conflict_warnings": plan["conflict_warnings"],
        "compatibility_unknown": plan["compatibility_unknown"],
        "post_install": (
            "A separate server restart/start is required before generated configs are inspected"
        ),
        "expected_result": (
            "The verified release is staged, backed up, installed, recorded, and reports "
            "restart_required"
        ),
    }


async def _apply_server_startup_update_summary(
    arguments: dict[str, Any], context: ToolContext, server: Server, base: dict[str, Any]
) -> dict[str, Any]:
    data = ApplyServerStartupPlanInput.model_validate(arguments)
    from services.server_startup_service import build_server_startup_plan

    plan = build_server_startup_plan(
        server,
        data.model_dump(exclude={"expected_plan_hash"}, exclude_unset=True),
    )
    if plan["plan_hash"] != data.expected_plan_hash:
        raise ValueError("Startup configuration changed before approval")
    if plan["blocked"]:
        raise ValueError("; ".join(plan["blocking_reasons"]))
    return {
        **base,
        "target": "CS2 startup settings",
        "configuration_revision": plan["configuration_revision"],
        "changes": plan["changes"],
        "steps": plan["steps"],
        "partial_failure_policy": plan["partial_failure_policy"],
        "expected_result": (
            "Settings are saved, the server is restarted, and process/A2S state is verified"
        ),
    }


async def _apply_managed_plugin_upgrade_summary(
    arguments: dict[str, Any], context: ToolContext, server: Server, base: dict[str, Any]
) -> dict[str, Any]:
    data = ApplyManagedPluginUpgradeInput.model_validate(arguments)
    from services.plugin_auto_update_service import plugin_auto_update_service

    plan = await plugin_auto_update_service.build_plugin_upgrade_plan(server.id, data.plugin_id)
    _, plan_hash = canonical_arguments(plan)
    if plan_hash != data.expected_plan_hash:
        raise ValueError("Managed plugin upgrade plan changed before approval")
    return {
        **base,
        "upgrade_plan": plan,
        "expected_plan_hash": plan_hash,
        "expected_result": "Only the selected managed plugin/framework is upgraded",
    }
