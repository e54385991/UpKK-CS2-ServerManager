"""Execute and resume individual AI tool runs."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func
from sqlmodel import col, select

from modules.models import AIConversation, AIMessage, AIRun, AIToolRun, Server, User
from services.compat import LateBoundModule

host = LateBoundModule("services.ai_orchestrator")


def _validate_write_tool_batch(tool_names: list[str]) -> None:
    write_tools = [name for name in tool_names if host.TOOLS_BY_NAME[name].risk == "write"]
    if len(write_tools) > 1:
        raise host.AIProviderError(
            "The assistant requested multiple write tools in one round; "
            "server changes must be planned and approved one at a time"
        )


async def _load_provider_messages(
    db,
    conversation: AIConversation,
    user: User,
    server: Server | None,
    admin_prompt: str,
    provider=None,
) -> list[dict[str, Any]]:
    """Build the provider request history for one round.

    With a provider config, history that no longer fits the configured context
    window is folded into the conversation's persisted summary instead of being
    dropped (``services.ai_context``). Without one — the Discord preview path
    and tests — the raw tail is returned unchanged.
    """
    prefix = [{"role": "system", "content": host.build_system_prompt(user, server, admin_prompt)}]
    history, truncated = await host.load_history(db, conversation)
    if provider is None:
        head = host.summary_message(conversation.summary)
        return [*prefix, *([head] if head else []), *[message for _, message in history]]
    result = await host.compact_if_needed(
        db, conversation, provider, prefix, history, truncated=truncated
    )
    return result.messages


async def _execute_tool_run(
    db,
    run: AIRun,
    tool_run: AIToolRun,
    user: User,
    server: Server | None,
    precomputed_result: dict[str, Any] | None = None,
) -> None:
    async def tool_event(event_type: str, payload: dict[str, Any]) -> None:
        if event_type in {"tool_progress", "diagnostic_progress"}:
            host._update_progress_snapshot(tool_run, payload)
            db.add(tool_run)
            await db.commit()
        await host._emit(
            run.id,
            event_type,
            {"tool_run_id": tool_run.id, "tool_name": tool_run.tool_name, **payload},
        )

    context = host.ToolContext(db=db, user=user, server=server, emit=tool_event)

    async def mark_running() -> None:
        tool_run.status = "running"
        host._update_progress_snapshot(tool_run, {"message": "Starting operation"})
        db.add(tool_run)
        await db.commit()
        await host._emit(
            run.id,
            "tool_started",
            {"tool_run_id": tool_run.id, "tool_name": tool_run.tool_name},
        )

    try:
        current_user = await db.get(User, user.id)
        if current_user is None or not current_user.is_active:
            raise PermissionError("The approving user is no longer active")
        if tool_run.requires_approval:
            _serialized, current_hash = host.canonical_arguments(tool_run.arguments)
            if current_hash != tool_run.arguments_hash:
                raise PermissionError("Tool arguments changed after approval")
            if tool_run.approved_by != user.id or tool_run.approved_at is None:
                raise PermissionError("Tool approval is not bound to the current user")
        if server is not None and server.id is not None:
            server = await host.authorized_server(db, current_user, server.id)
            context.server = server
        context.user = current_user
        context.run_id = run.id
        if precomputed_result is not None:
            await mark_running()
            result = precomputed_result
        elif tool_run.risk == "write":
            host._update_progress_snapshot(tool_run, {"message": "Queued for execution"})
            db.add(tool_run)
            await db.commit()
            await host._emit(
                run.id,
                "tool_queued",
                {"tool_run_id": tool_run.id, "tool_name": tool_run.tool_name},
            )
            # Positive IDs are server locks. A reserved negative namespace gives
            # each principal one cross-process AI write lease without nesting the
            # same server lock used by the underlying business service. The tool
            # remains queued until it owns the lease, so later approvals wait
            # instead of failing with an active-write conflict.
            async with host.maintenance_lock_service.get(
                -(current_user.id + 1),
                operation="ai_user_write",
                wait=True,
                wait_timeout=host.AI_WRITE_QUEUE_WAIT_SECONDS,
                ttl=host.AI_WRITE_LOCK_TTL,
            ):
                await mark_running()
                result = await host.execute_tool(tool_run.tool_name, tool_run.arguments, context)
        else:
            await mark_running()
            result = await host.execute_tool(tool_run.tool_name, tool_run.arguments, context)
    except Exception as exc:
        safe_error = host.redact_sensitive_text(str(exc), limit=2000)
        host.audit_security_event(
            "tool_execution_rejected",
            user_id=user.id,
            server_id=server.id if server is not None else None,
            operation=tool_run.tool_name,
            detail=safe_error,
        )
        tool_run.status = "failed"
        tool_run.error = safe_error
        tool_run.result = {"success": False, "error": safe_error}
        host._finalize_progress_snapshot(tool_run, success=False, message=safe_error)
        event_type = "tool_failed"
    else:
        tool_run.result = result
        result_failed = isinstance(result, dict) and result.get("success") is False
        if result_failed:
            safe_error = host.redact_sensitive_text(
                str(result.get("error") or result.get("message") or "Tool execution failed"),
                limit=2000,
            )
            tool_run.status = "failed"
            tool_run.error = safe_error
            host._finalize_progress_snapshot(tool_run, success=False, message=safe_error)
            event_type = "tool_failed"
        else:
            tool_run.status = "completed"
            host._finalize_progress_snapshot(tool_run, success=True, message="Operation completed")
            event_type = "tool_completed"
    tool_run.completed_at = host.get_current_time()
    db.add(tool_run)
    db.add(
        AIMessage(
            conversation_id=run.conversation_id,
            role="tool",
            content=json.dumps(tool_run.result, ensure_ascii=False, default=str),
            tool_call_id=tool_run.tool_call_id,
            tool_name=tool_run.tool_name,
            visible=False,
        )
    )
    await db.commit()
    await host._emit(
        run.id,
        event_type,
        {
            "tool_run_id": tool_run.id,
            "tool_name": tool_run.tool_name,
            "result": tool_run.result,
        },
    )


async def _resume_decided_tools(db, run: AIRun, user: User, server: Server | None) -> bool:
    result = await db.execute(
        select(AIToolRun)
        .where(
            AIToolRun.run_id == run.id,
            col(AIToolRun.status).in_(("approved", "queued", "rejected", "pending_approval")),
        )
        .order_by(col(AIToolRun.created_at).asc(), col(AIToolRun.id).asc())
    )
    items = list(result.scalars().all())
    for item in items:
        if item.status in ("approved", "queued"):
            await host._execute_tool_run(db, run, item, user, server)
        elif item.status == "rejected":
            if item.completed_at is not None:
                continue
            item.result = {"success": False, "error": "denied_by_user"}
            item.completed_at = host.get_current_time()
            host._finalize_progress_snapshot(
                item, success=False, message="Denied by user", interrupted=True
            )
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
            await db.commit()
            await host._emit(
                run.id,
                "tool_rejected",
                {"tool_run_id": item.id, "tool_name": item.tool_name},
            )
    pending_result = await db.execute(
        select(func.count())
        .select_from(AIToolRun)
        .where(
            AIToolRun.run_id == run.id,
            AIToolRun.status == "pending_approval",
        )
    )
    return int(pending_result.scalar_one()) > 0
