"""Workshop map planning and apply tools."""

from __future__ import annotations

from typing import Any

from services.ai.tools.context import ToolContext, tools
from services.ai.tools.schemas import ApplyWorkshopPlanInput, WorkshopPlanInput


async def plan_workshop_map(ctx: ToolContext, data: WorkshopPlanInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    from services.workshop_map_service import build_workshop_map_plan

    return await build_workshop_map_plan(ctx.db, server, data.model_dump())


async def apply_workshop_map(ctx: ToolContext, data: ApplyWorkshopPlanInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    from services.workshop_map_service import execute_workshop_map_plan

    return await execute_workshop_map_plan(
        ctx.db,
        server,
        ctx.user,
        data.model_dump(exclude={"acknowledge_warning_rule_ids", "expected_plan_hash"}),
        set(data.acknowledge_warning_rule_ids),
        expected_plan_hash=data.expected_plan_hash,
        progress=lambda message, message_type, metadata=None: ctx.emit(
            "tool_progress",
            {"message": message, "message_type": message_type, **(metadata or {})},
        ),
        lock_operation="ai:workshop_map_plan",
        operation_id=ctx.run_id,
    )
