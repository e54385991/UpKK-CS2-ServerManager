"""Approval step formatting and waiting-run reconciliation."""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import col, select

from modules.models import AIMessage, AIRun, AIToolRun
from services.compat import LateBoundModule

host = LateBoundModule("services.ai_orchestrator")


def _approval_step_id(tool_name: str, step: Any, index: int) -> str:
    if isinstance(step, dict):
        if tool_name == "apply_plugin_plan" and step.get("plugin_id") is not None:
            return f"plugin:{step['plugin_id']}"
        action = str(step.get("action") or "")
        if tool_name == "apply_workshop_map":
            if action == "install_framework":
                framework = str(step.get("framework") or "").lower()
                if framework == "metamod":
                    return "install_metamod"
                if framework == "counterstrikesharp":
                    return "install_counterstrikesharp"
            if action == "install_market_plugin":
                return "install_mapchooser"
            if action in {"restart_server", "patch_plugin_config", "append_map", "verify"}:
                return action
        if tool_name == "apply_server_startup_update" and action in {
            "validate_startup_revision",
            "save_startup_settings",
            "restart_server",
            "verify_server",
        }:
            return action
        if action:
            return f"step:{index + 1}:{action}"
    return f"step:{index + 1}"


def _approval_step_label(step: Any) -> str:
    if isinstance(step, str):
        return host.redact_sensitive_text(step, limit=500)
    if not isinstance(step, dict):
        return "Planned operation"
    action = str(step.get("action") or "")
    if action == "install_framework":
        return f"Install {step.get('framework') or 'framework'}"
    if action == "install_market_plugin":
        return f"Install {step.get('title') or 'plugin'}"
    if action == "restart_server":
        return "Restart server"
    if action == "validate_startup_revision":
        return "Validate startup configuration revision"
    if action == "save_startup_settings":
        return "Save startup settings"
    if action == "verify_server":
        return "Verify process and A2S"
    if action == "patch_plugin_config":
        return "Update MapChooser configuration"
    if action == "append_map":
        return f"Add map {step.get('name') or step.get('workshop_id') or ''}".strip()
    if action == "verify":
        return "Verify installation"
    title = step.get("title") or step.get("name") or action or "Planned operation"
    prefix = "Skip" if step.get("status") == "already_installed" else "Install"
    if step.get("plugin_id") is not None:
        return host.redact_sensitive_text(f"{prefix} {title}", limit=500)
    return host.redact_sensitive_text(str(title).replace("_", " ").strip(), limit=500)


def _build_plan_snapshots(tool_name: str, summary: dict[str, Any]) -> tuple[dict, dict]:
    safe_summary = host.sanitize_tool_result(summary)
    plan = dict(safe_summary) if isinstance(safe_summary, dict) else {}
    now = host.get_current_time().isoformat()
    normalized_steps = []
    for index, step in enumerate(summary.get("steps") or []):
        skipped = isinstance(step, dict) and step.get("status") == "already_installed"
        normalized_steps.append(
            {
                "id": _approval_step_id(tool_name, step, index),
                "label": _approval_step_label(step),
                "status": "skipped" if skipped else "pending",
                "started_at": None,
                "completed_at": now if skipped else None,
            }
        )
    plan["version"] = 1
    plan["steps"] = normalized_steps
    progress = {
        "version": 1,
        "steps": [dict(step) for step in normalized_steps],
        "current_step": None,
        "message": "Waiting for approval",
        "completed": sum(step["status"] in {"completed", "skipped"} for step in normalized_steps),
        "total": len(normalized_steps),
    }
    return plan, progress


def _approval_is_expired(item: AIToolRun, now) -> bool:
    expires_at = item.approval_expires_at
    if expires_at is None:
        return True
    comparable_now = now.replace(tzinfo=None) if expires_at.tzinfo is None else now
    return expires_at <= comparable_now


async def _close_unexecuted_tools(
    db,
    run: AIRun,
    tools: list[AIToolRun],
    *,
    expired_ids: set[str],
    cancellation_error: str = "Cancelled because another approval in the same run expired",
    cancellation_status: str = "cancelled",
) -> None:
    now = host.get_current_time()
    for item in tools:
        expired = item.id in expired_ids
        error = "Approval expired before execution" if expired else cancellation_error
        item.status = "expired" if expired else cancellation_status
        item.error = error
        item.result = {"success": False, "error": error}
        item.completed_at = now
        host._finalize_progress_snapshot(item, success=False, message=error, interrupted=True)
        db.add(item)
        db.add(
            AIMessage(
                conversation_id=run.conversation_id,
                role="tool",
                content=json.dumps(item.result),
                tool_call_id=item.tool_call_id,
                tool_name=item.tool_name,
                visible=False,
            )
        )


async def reconcile_waiting_approval_runs(
    db,
    *,
    user_id: int | None = None,
    conversation_id: str | None = None,
    run_id: str | None = None,
) -> set[str]:
    """Finish expired or legacy multi-write approval batches without executing them."""
    filters = [AIRun.status == "waiting_approval"]
    if user_id is not None:
        filters.append(AIRun.user_id == user_id)
    if conversation_id is not None:
        filters.append(AIRun.conversation_id == conversation_id)
    if run_id is not None:
        filters.append(AIRun.id == run_id)
    run_result = await db.execute(select(AIRun).where(*filters).with_for_update())
    runs = list(run_result.scalars().all())
    if not runs:
        return set()

    tool_result = await db.execute(
        select(AIToolRun)
        .where(
            col(AIToolRun.run_id).in_([run.id for run in runs]),
            col(AIToolRun.status).in_(("pending_approval", "approved", "queued")),
        )
        .with_for_update()
    )
    tools_by_run: dict[str, list[AIToolRun]] = {}
    for item in tool_result.scalars().all():
        tools_by_run.setdefault(item.run_id, []).append(item)

    now = host.get_current_time()
    terminal_run_ids: set[str] = set()
    for run in runs:
        tools = tools_by_run.get(run.id, [])
        invalid_batch = sum(item.risk == "write" for item in tools) > 1
        expired_ids = {
            item.id
            for item in tools
            if item.status == "pending_approval" and _approval_is_expired(item, now)
        }
        if not expired_ids and not invalid_batch:
            continue
        if invalid_batch:
            run.status = "cancelled"
            run.error = (
                "Cancelled a legacy approval batch containing multiple server changes; "
                "request a fresh plan"
            )
            await host._close_unexecuted_tools(
                db,
                run,
                tools,
                expired_ids=set(),
                cancellation_error=run.error,
            )
        else:
            run.status = "expired"
            run.error = "One or more tool approvals expired before execution"
            await host._close_unexecuted_tools(db, run, tools, expired_ids=expired_ids)
        run.completed_at = now
        db.add(run)
        host._add_run_error_message(db, run, run.error)
        terminal_run_ids.add(run.id)
    if terminal_run_ids:
        await db.commit()
    return terminal_run_ids
