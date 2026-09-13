"""GitHub discovery and install tools for the AI assistant."""

from __future__ import annotations

from typing import Any

from services.ai.tools.context import (
    ToolContext,
    tools,
)
from services.ai.tools.schemas import (
    GitHubApplyInput,
    GitHubInspectInput,
    GitHubPlanInput,
    GitHubSearchInput,
)


async def _optional_linux_runtime_profile(ctx: ToolContext) -> dict[str, Any] | None:
    if ctx.server is None or ctx.server.id is None:
        return None
    server = await tools.authorized_server(ctx.db, ctx.user, ctx.server.id)
    from services.linux_runtime_service import detect_linux_runtime_profile

    return await detect_linux_runtime_profile(server)


async def search_github_cs2_plugins(ctx: ToolContext, data: GitHubSearchInput) -> dict[str, Any]:
    user = await tools._require_active_user(ctx)
    await tools.enforce_agent_rate_limit(user.id, "github_search", limit=10)
    from services.github_plugin_plan_service import search_github_plugins

    runtime_profile = await _optional_linux_runtime_profile(ctx)
    return await search_github_plugins(
        ctx.db,
        user,
        data.query,
        limit=3,
        linux_runtime_profile=runtime_profile,
    )


async def inspect_github_plugin(ctx: ToolContext, data: GitHubInspectInput) -> dict[str, Any]:
    user = await tools._require_active_user(ctx)
    await tools.enforce_agent_rate_limit(user.id, "github_inspect", limit=15)
    from services.github_plugin_plan_service import inspect_github_plugin as inspect_service

    runtime_profile = await _optional_linux_runtime_profile(ctx)
    return await inspect_service(
        ctx.db,
        user,
        data.repo_url,
        data.mode,
        runtime_profile,
    )


async def plan_github_plugin_install(ctx: ToolContext, data: GitHubPlanInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    await tools.enforce_agent_rate_limit(ctx.user.id, "github_plan", limit=5)
    from modules.schemas.plugins import GitHubPluginInstallPlanRequest
    from services.github_plugin_plan_service import build_github_install_plan

    request = GitHubPluginInstallPlanRequest.model_validate(data.model_dump())
    return await build_github_install_plan(ctx.db, ctx.user, server.id, request)


async def apply_github_plugin_install(ctx: ToolContext, data: GitHubApplyInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    await tools.enforce_agent_rate_limit(ctx.user.id, "github_install", limit=2, window_seconds=300)
    from modules.schemas.plugins import GitHubPluginInstallPlanRequest
    from services.github_plugin_plan_service import execute_github_install_plan

    request = GitHubPluginInstallPlanRequest.model_validate(
        data.model_dump(
            exclude={
                "expected_plan_hash",
                "acknowledge_warning_rule_ids",
                "acknowledge_unknown_compatibility",
            }
        )
    )
    return await execute_github_install_plan(
        ctx.db,
        ctx.user,
        server.id,
        request,
        data.expected_plan_hash,
        set(data.acknowledge_warning_rule_ids),
        data.acknowledge_unknown_compatibility,
        progress=lambda message, message_type, metadata=None: ctx.emit(
            "tool_progress",
            {"message": message, "message_type": message_type, **(metadata or {})},
        ),
        lock_operation="ai:github_plugin_install_plan",
        operation_id=ctx.run_id,
    )
