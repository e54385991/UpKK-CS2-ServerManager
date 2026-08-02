"""Bounded OpenAI Chat Completions tool-calling orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from modules.database import async_session_maker
from modules.models import (
    AIConversation,
    AIMessage,
    AIRun,
    AISystemSettings,
    AIToolRun,
    Server,
    User,
)
from modules.utils import get_current_time
from services.ai_access import audit_security_event, authorized_server
from services.ai_events import ai_event_hub
from services.ai_prompt import build_system_prompt
from services.ai_provider import AIProviderError, create_chat_completion
from services.ai_security import (
    AIConfigurationError,
    get_effective_provider,
    redact_sensitive_text,
    sanitize_tool_result,
)
from services.ai_tools import (
    TOOLS_BY_NAME,
    ToolContext,
    build_approval_summary,
    canonical_arguments,
    execute_tool,
    tool_definitions,
)
from services.maintenance_lock import maintenance_lock_service
from services.redis_manager import redis_manager

logger = logging.getLogger(__name__)
DEFAULT_MAX_PROVIDER_ROUNDS = 200
DEFAULT_MAX_TOOL_CALLS_PER_ROUND = 200
MAX_CONFIGURED_AI_LIMIT = 1000
MAX_REPEATED_CALLS = 3
ACTIVE_RUN_STATUSES = ("queued", "running", "waiting_approval")
AI_DELTA_EVENT_CHARS = 96
AI_WRITE_QUEUE_WAIT_SECONDS = 5 * 60
AI_WRITE_LOCK_TTL = 5 * 60
AI_RETRY_MAX_ATTEMPTS = 5
AI_RETRY_BASE_SECONDS = 15
AI_BACKGROUND_TASK_RETENTION_MINUTES = 10
RUN_ERROR_TOOL_NAME = "__run_error__"
TERMINAL_RUN_STATUSES = ("completed", "failed", "interrupted", "expired", "cancelled")
STEP_STATUSES = {"pending", "running", "completed", "failed", "skipped", "interrupted"}
TERMINAL_STEP_STATUSES = {"completed", "failed", "skipped", "interrupted"}


async def _emit(run_id: str, event_type: str, payload: dict[str, Any]) -> None:
    await ai_event_hub.emit(run_id, event_type, payload)


def _add_run_error_message(db, run: AIRun, message: str) -> None:
    db.add(
        AIMessage(
            conversation_id=run.conversation_id,
            role="assistant",
            content=redact_sensitive_text(message, limit=2000),
            tool_name=RUN_ERROR_TOOL_NAME,
            visible=True,
        )
    )


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
            if action in {"patch_plugin_config", "append_map", "verify"}:
                return action
        if action:
            return f"step:{index + 1}:{action}"
    return f"step:{index + 1}"


def _approval_step_label(step: Any) -> str:
    if isinstance(step, str):
        return redact_sensitive_text(step, limit=500)
    if not isinstance(step, dict):
        return "Planned operation"
    action = str(step.get("action") or "")
    if action == "install_framework":
        return f"Install {step.get('framework') or 'framework'}"
    if action == "install_market_plugin":
        return f"Install {step.get('title') or 'plugin'}"
    if action == "patch_plugin_config":
        return "Update MapChooser configuration"
    if action == "append_map":
        return f"Add map {step.get('name') or step.get('workshop_id') or ''}".strip()
    if action == "verify":
        return "Verify installation"
    title = step.get("title") or step.get("name") or action or "Planned operation"
    prefix = "Skip" if step.get("status") == "already_installed" else "Install"
    if step.get("plugin_id") is not None:
        return redact_sensitive_text(f"{prefix} {title}", limit=500)
    return redact_sensitive_text(str(title).replace("_", " ").strip(), limit=500)


def _build_plan_snapshots(tool_name: str, summary: dict[str, Any]) -> tuple[dict, dict]:
    safe_summary = sanitize_tool_result(summary)
    plan = dict(safe_summary) if isinstance(safe_summary, dict) else {}
    now = get_current_time().isoformat()
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


def _update_progress_snapshot(tool_run: AIToolRun, payload: dict[str, Any]) -> None:
    previous = getattr(tool_run, "progress_snapshot", None)
    snapshot = dict(previous) if isinstance(previous, dict) else {"version": 1, "steps": []}
    steps = [dict(step) for step in snapshot.get("steps") or [] if isinstance(step, dict)]
    now = get_current_time()
    step_id = str(payload.get("step_id") or "")
    step_status = str(payload.get("step_status") or "")
    if step_status not in STEP_STATUSES:
        step_status = ""
    if step_id and step_status:
        for step in steps:
            if str(step.get("id")) != step_id:
                continue
            step["status"] = step_status
            if step_status == "running" and not step.get("started_at"):
                step["started_at"] = now.isoformat()
            if step_status in TERMINAL_STEP_STATUSES:
                step["completed_at"] = now.isoformat()
            break
    running = next((step for step in steps if step.get("status") == "running"), None)
    snapshot.update(
        {
            "version": 1,
            "steps": steps,
            "current_step": running.get("id") if running else None,
            "message": redact_sensitive_text(str(payload.get("message") or ""), limit=2000),
            "completed": sum(step.get("status") in {"completed", "skipped"} for step in steps),
            "total": len(steps),
        }
    )
    tool_run.progress_snapshot = snapshot
    tool_run.progress_updated_at = now


def _finalize_progress_snapshot(
    tool_run: AIToolRun, *, success: bool, message: str, interrupted: bool = False
) -> None:
    snapshot = getattr(tool_run, "progress_snapshot", None)
    if not isinstance(snapshot, dict):
        return
    steps = [dict(step) for step in snapshot.get("steps") or [] if isinstance(step, dict)]
    now = get_current_time()
    failure_recorded = any(step.get("status") == "failed" for step in steps)
    for step in steps:
        if step.get("status") in TERMINAL_STEP_STATUSES:
            continue
        if success:
            status = "completed"
        elif interrupted:
            status = "interrupted"
        elif step.get("status") == "running" or not failure_recorded:
            status = "failed"
            failure_recorded = True
        else:
            status = "interrupted"
        step["status"] = status
        step["started_at"] = step.get("started_at") or now.isoformat()
        step["completed_at"] = now.isoformat()
    snapshot.update(
        {
            "steps": steps,
            "current_step": None,
            "message": redact_sensitive_text(message, limit=2000),
            "completed": sum(step.get("status") in {"completed", "skipped"} for step in steps),
            "total": len(steps),
        }
    )
    tool_run.progress_snapshot = snapshot
    tool_run.progress_updated_at = now


class _AssistantDeltaEmitter:
    """Coalesce provider tokens into bounded, responsive browser events."""

    def __init__(self, run_id: str, round_index: int) -> None:
        self.run_id = run_id
        self.round_index = round_index
        self.buffer = ""
        self.last_emit = time.monotonic()

    async def add(self, delta: str) -> None:
        self.buffer += delta
        now = time.monotonic()
        if len(self.buffer) >= AI_DELTA_EVENT_CHARS or now - self.last_emit >= 0.1:
            await self.flush()

    async def flush(self) -> None:
        if not self.buffer:
            return
        delta, self.buffer = self.buffer, ""
        self.last_emit = time.monotonic()
        await _emit(
            self.run_id,
            "assistant_delta",
            {"round": self.round_index, "delta": delta},
        )


def _retry_delay_seconds(retry_attempt: int) -> int:
    return AI_RETRY_BASE_SECONDS * (2 ** (retry_attempt - 1))


async def _create_provider_response_with_retry(
    provider,
    messages: list[dict[str, Any]],
    *,
    run_id: str,
    round_index: int,
    server_selected: bool,
) -> dict[str, Any]:
    for retry_attempt in range(AI_RETRY_MAX_ATTEMPTS + 1):
        delta_emitter = _AssistantDeltaEmitter(run_id, round_index)
        try:
            response = await create_chat_completion(
                provider,
                messages,
                tools=tool_definitions(server_selected=server_selected),
                stream=True,
                on_text_delta=delta_emitter.add,
            )
            response_content = str(response.get("content") or "")
            if not response.get("tool_calls") and not response_content.strip():
                raise AIProviderError("AI provider returned neither text nor tool calls")
            await delta_emitter.flush()
        except AIProviderError as exc:
            delta_emitter.buffer = ""
            if retry_attempt >= AI_RETRY_MAX_ATTEMPTS:
                raise
            next_attempt = retry_attempt + 1
            delay = _retry_delay_seconds(next_attempt)
            safe_error = redact_sensitive_text(str(exc), limit=2000)
            await _emit(
                run_id,
                "run_retrying",
                {
                    "round": round_index,
                    "attempt": next_attempt,
                    "max_attempts": AI_RETRY_MAX_ATTEMPTS,
                    "delay_seconds": delay,
                    "error": safe_error,
                },
            )
            await asyncio.sleep(delay)
            continue
        return response
    raise AIProviderError("AI provider retry loop ended unexpectedly")


async def _fail_run(db, run: AIRun, message: str) -> None:
    safe_message = redact_sensitive_text(message, limit=2000)
    run.status = "failed"
    run.error = safe_message
    run.completed_at = get_current_time()
    db.add(run)
    _add_run_error_message(db, run, safe_message)
    await db.commit()
    await _emit(run.id, "run_failed", {"error": safe_message})


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
    now = get_current_time()
    for item in tools:
        expired = item.id in expired_ids
        error = "Approval expired before execution" if expired else cancellation_error
        item.status = "expired" if expired else cancellation_status
        item.error = error
        item.result = {"success": False, "error": error}
        item.completed_at = now
        _finalize_progress_snapshot(item, success=False, message=error, interrupted=True)
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
            AIToolRun.run_id.in_([run.id for run in runs]),
            AIToolRun.status.in_(("pending_approval", "approved", "queued")),
        )
        .with_for_update()
    )
    tools_by_run: dict[str, list[AIToolRun]] = {}
    for item in tool_result.scalars().all():
        tools_by_run.setdefault(item.run_id, []).append(item)

    now = get_current_time()
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
            await _close_unexecuted_tools(
                db,
                run,
                tools,
                expired_ids=set(),
                cancellation_error=run.error,
            )
        else:
            run.status = "expired"
            run.error = "One or more tool approvals expired before execution"
            await _close_unexecuted_tools(db, run, tools, expired_ids=expired_ids)
        run.completed_at = now
        db.add(run)
        _add_run_error_message(db, run, run.error)
        terminal_run_ids.add(run.id)
    if terminal_run_ids:
        await db.commit()
    return terminal_run_ids


async def cleanup_expired_ai_runs(
    db,
    *,
    user_id: int | None = None,
) -> int:
    """Delete terminal run metadata after the background-task visibility window."""
    cutoff = get_current_time() - timedelta(minutes=AI_BACKGROUND_TASK_RETENTION_MINUTES)
    filters = [
        AIRun.status.in_(TERMINAL_RUN_STATUSES),
        func.coalesce(AIRun.completed_at, AIRun.updated_at, AIRun.created_at) < cutoff,
    ]
    if user_id is not None:
        filters.append(AIRun.user_id == user_id)
    result = await db.execute(select(AIRun).where(*filters))
    runs = list(result.scalars().all())
    for run in runs:
        await db.delete(run)
    if runs:
        await db.commit()
    return len(runs)


def _validate_write_tool_batch(tool_names: list[str]) -> None:
    write_tools = [name for name in tool_names if TOOLS_BY_NAME[name].risk == "write"]
    if len(write_tools) > 1:
        raise AIProviderError(
            "The assistant requested multiple write tools in one round; "
            "server changes must be planned and approved one at a time"
        )


async def _load_provider_messages(
    db, conversation: AIConversation, user: User, server: Server | None, admin_prompt: str
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(AIMessage)
        .where(AIMessage.conversation_id == conversation.id)
        .order_by(AIMessage.id.desc())
        .limit(120)
    )
    stored = list(reversed(result.scalars().all()))
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(user, server, admin_prompt)}
    ]
    running_chars = 0
    converted: list[dict[str, Any]] = []
    for item in reversed(stored):
        content = item.content or ""
        running_chars += len(content)
        if running_chars > 100_000:
            break
        message: dict[str, Any] = {"role": item.role, "content": item.content}
        if item.tool_calls:
            message["tool_calls"] = item.tool_calls
        if item.tool_call_id:
            message["tool_call_id"] = item.tool_call_id
        if item.tool_name:
            message["name"] = item.tool_name
        converted.append(message)
    messages.extend(reversed(converted))
    return messages


async def _execute_tool_run(
    db,
    run: AIRun,
    tool_run: AIToolRun,
    user: User,
    server: Server | None,
) -> None:
    async def tool_event(event_type: str, payload: dict[str, Any]) -> None:
        if event_type in {"tool_progress", "diagnostic_progress"}:
            _update_progress_snapshot(tool_run, payload)
            db.add(tool_run)
            await db.commit()
        await _emit(
            run.id,
            event_type,
            {"tool_run_id": tool_run.id, "tool_name": tool_run.tool_name, **payload},
        )

    context = ToolContext(db=db, user=user, server=server, emit=tool_event)

    async def mark_running() -> None:
        tool_run.status = "running"
        _update_progress_snapshot(tool_run, {"message": "Starting operation"})
        db.add(tool_run)
        await db.commit()
        await _emit(
            run.id,
            "tool_started",
            {"tool_run_id": tool_run.id, "tool_name": tool_run.tool_name},
        )

    try:
        current_user = await db.get(User, user.id)
        if current_user is None or not current_user.is_active:
            raise PermissionError("The approving user is no longer active")
        if tool_run.requires_approval:
            _serialized, current_hash = canonical_arguments(tool_run.arguments)
            if current_hash != tool_run.arguments_hash:
                raise PermissionError("Tool arguments changed after approval")
            if tool_run.approved_by != user.id or tool_run.approved_at is None:
                raise PermissionError("Tool approval is not bound to the current user")
        if server is not None and server.id is not None:
            server = await authorized_server(db, current_user, server.id)
            context.server = server
        context.user = current_user
        context.run_id = run.id
        if tool_run.risk == "write":
            _update_progress_snapshot(tool_run, {"message": "Queued for execution"})
            db.add(tool_run)
            await db.commit()
            await _emit(
                run.id,
                "tool_queued",
                {"tool_run_id": tool_run.id, "tool_name": tool_run.tool_name},
            )
            # Positive IDs are server locks. A reserved negative namespace gives
            # each principal one cross-process AI write lease without nesting the
            # same server lock used by the underlying business service. The tool
            # remains queued until it owns the lease, so later approvals wait
            # instead of failing with an active-write conflict.
            async with maintenance_lock_service.get(
                -(current_user.id + 1),
                operation="ai_user_write",
                wait=True,
                wait_timeout=AI_WRITE_QUEUE_WAIT_SECONDS,
                ttl=AI_WRITE_LOCK_TTL,
            ):
                await mark_running()
                result = await execute_tool(tool_run.tool_name, tool_run.arguments, context)
        else:
            await mark_running()
            result = await execute_tool(tool_run.tool_name, tool_run.arguments, context)
    except Exception as exc:
        safe_error = redact_sensitive_text(str(exc), limit=2000)
        audit_security_event(
            "tool_execution_rejected",
            user_id=user.id,
            server_id=server.id if server is not None else None,
            operation=tool_run.tool_name,
            detail=safe_error,
        )
        tool_run.status = "failed"
        tool_run.error = safe_error
        tool_run.result = {"success": False, "error": safe_error}
        _finalize_progress_snapshot(tool_run, success=False, message=safe_error)
        event_type = "tool_failed"
    else:
        tool_run.result = result
        result_failed = isinstance(result, dict) and result.get("success") is False
        if result_failed:
            safe_error = redact_sensitive_text(
                str(result.get("error") or result.get("message") or "Tool execution failed"),
                limit=2000,
            )
            tool_run.status = "failed"
            tool_run.error = safe_error
            _finalize_progress_snapshot(tool_run, success=False, message=safe_error)
            event_type = "tool_failed"
        else:
            tool_run.status = "completed"
            _finalize_progress_snapshot(tool_run, success=True, message="Operation completed")
            event_type = "tool_completed"
    tool_run.completed_at = get_current_time()
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
    await _emit(
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
            AIToolRun.status.in_(("approved", "queued", "rejected", "pending_approval")),
        )
        .order_by(AIToolRun.created_at.asc(), AIToolRun.id.asc())
    )
    items = list(result.scalars().all())
    for item in items:
        if item.status in ("approved", "queued"):
            await _execute_tool_run(db, run, item, user, server)
        elif item.status == "rejected":
            if item.completed_at is not None:
                continue
            item.result = {"success": False, "error": "denied_by_user"}
            item.completed_at = get_current_time()
            _finalize_progress_snapshot(
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
            await _emit(
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


async def process_ai_run(run_id: str) -> None:
    """Run or resume one conversation job. Exceptions become persisted failures."""
    async with async_session_maker() as db:
        run = await db.get(AIRun, run_id)
        if run is None or run.status not in ACTIVE_RUN_STATUSES:
            return
        conversation = await db.get(AIConversation, run.conversation_id)
        user = await db.get(User, run.user_id)
        if conversation is None or user is None or conversation.user_id != user.id:
            await _fail_run(db, run, "Conversation owner is unavailable")
            return
        server = None
        if run.server_id is not None:
            server = (
                await Server.get_by_id(db, run.server_id)
                if user.is_admin
                else await Server.get_by_id_and_user(db, run.server_id, user.id)
            )
            if server is None:
                await _fail_run(db, run, "Selected server is no longer available")
                return
        try:
            provider = await get_effective_provider(db, user)
        except AIConfigurationError as exc:
            await _fail_run(db, run, str(exc))
            return
        if provider is None:
            await _fail_run(db, run, "No tested AI provider is enabled")
            return

        settings = await AISystemSettings.get_or_create(db)
        max_rounds = min(
            max(int(getattr(settings, "max_provider_rounds", 0) or DEFAULT_MAX_PROVIDER_ROUNDS), 1),
            MAX_CONFIGURED_AI_LIMIT,
        )
        max_tool_calls_per_round = min(
            max(
                int(
                    getattr(settings, "max_tool_calls_per_round", 0)
                    or DEFAULT_MAX_TOOL_CALLS_PER_ROUND
                ),
                1,
            ),
            MAX_CONFIGURED_AI_LIMIT,
        )

        run.status = "running"
        run.error = None
        db.add(run)
        await db.commit()
        await _emit(run.id, "run_started", {"status": run.status})

        try:
            if await _resume_decided_tools(db, run, user, server):
                run.status = "waiting_approval"
                db.add(run)
                await db.commit()
                return

            last_user_result = await db.execute(
                select(func.max(AIMessage.id)).where(
                    AIMessage.conversation_id == conversation.id,
                    AIMessage.role == "user",
                )
            )
            last_user_message_id = int(last_user_result.scalar_one() or 0)
            count_result = await db.execute(
                select(func.count())
                .select_from(AIMessage)
                .where(
                    AIMessage.conversation_id == conversation.id,
                    AIMessage.role == "assistant",
                    AIMessage.id > last_user_message_id,
                )
            )
            rounds_used = int(count_result.scalar_one())
            tool_result = await db.execute(select(AIToolRun).where(AIToolRun.run_id == run.id))
            signatures = Counter(
                (item.tool_name, item.arguments_hash) for item in tool_result.scalars().all()
            )

            while rounds_used < max_rounds:
                await db.refresh(run)
                if run.status == "interrupted":
                    await _emit(run.id, "run_interrupted", {"error": run.error or "Interrupted"})
                    return
                messages = await _load_provider_messages(
                    db, conversation, user, server, provider.admin_prompt
                )
                round_index = rounds_used + 1
                response = await _create_provider_response_with_retry(
                    provider,
                    messages,
                    run_id=run.id,
                    round_index=round_index,
                    server_selected=server is not None,
                )
                rounds_used += 1
                content = redact_sensitive_text(str(response.get("content") or ""), limit=20_000)
                calls = response.get("tool_calls")
                if not calls:
                    if not content.strip():
                        raise AIProviderError("AI provider returned neither text nor tool calls")
                    assistant = AIMessage(
                        conversation_id=conversation.id,
                        role="assistant",
                        content=content,
                        visible=True,
                    )
                    db.add(assistant)
                    conversation.updated_at = get_current_time()
                    run.status = "completed"
                    run.completed_at = get_current_time()
                    db.add(conversation)
                    db.add(run)
                    await db.commit()
                    await db.refresh(assistant)
                    await _emit(
                        run.id,
                        "assistant_message",
                        {
                            "message_id": assistant.id,
                            "round": round_index,
                            "content": content,
                        },
                    )
                    await _emit(run.id, "run_completed", {"status": run.status})
                    return
                if not isinstance(calls, list) or len(calls) > max_tool_calls_per_round:
                    raise AIProviderError(
                        f"Tool-call limit exceeded ({max_tool_calls_per_round} per round)"
                    )

                normalized_calls: list[tuple[dict[str, Any], str, dict[str, Any], str]] = []
                seen_ids: set[str] = set()
                for raw_call in calls:
                    if not isinstance(raw_call, dict):
                        raise AIProviderError("AI provider returned an invalid tool call")
                    call_id = str(raw_call.get("id") or "")
                    function = raw_call.get("function")
                    if (
                        not call_id
                        or len(call_id) > 100
                        or call_id in seen_ids
                        or not isinstance(function, dict)
                    ):
                        raise AIProviderError("AI provider returned an invalid tool call ID")
                    seen_ids.add(call_id)
                    name = str(function.get("name") or "")
                    spec = TOOLS_BY_NAME.get(name)
                    if spec is None:
                        raise AIProviderError(f"AI provider requested unknown tool: {name}")
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise AIProviderError(
                            f"AI provider supplied invalid JSON for {name}"
                        ) from exc
                    if not isinstance(arguments, dict):
                        raise AIProviderError(f"Tool arguments for {name} must be an object")
                    try:
                        validated = spec.input_model.model_validate(arguments)
                    except ValidationError as exc:
                        raise AIProviderError(
                            f"Invalid arguments for {name}: {exc.errors(include_url=False)}"
                        ) from exc
                    clean_arguments = validated.model_dump(mode="json")
                    _, arguments_hash = canonical_arguments(clean_arguments)
                    signatures[(name, arguments_hash)] += 1
                    if signatures[(name, arguments_hash)] > MAX_REPEATED_CALLS:
                        raise AIProviderError(f"Repeated tool-call loop detected for {name}")
                    normalized_calls.append((raw_call, name, clean_arguments, arguments_hash))

                _validate_write_tool_batch([item[1] for item in normalized_calls])

                assistant_turn = AIMessage(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=content or None,
                    tool_calls=calls,
                    visible=bool(content.strip()),
                )
                db.add(assistant_turn)
                created: list[AIToolRun] = []
                for raw_call, name, arguments, arguments_hash in normalized_calls:
                    spec = TOOLS_BY_NAME[name]
                    item = AIToolRun(
                        run_id=run.id,
                        tool_call_id=str(raw_call["id"]),
                        tool_name=name,
                        arguments=arguments,
                        arguments_hash=arguments_hash,
                        risk=spec.risk,
                        requires_approval=spec.risk == "write",
                        status="pending_approval" if spec.risk == "write" else "pending",
                        approval_expires_at=(
                            get_current_time() + timedelta(minutes=15)
                            if spec.risk == "write"
                            else None
                        ),
                    )
                    db.add(item)
                    created.append(item)
                await db.commit()
                await db.refresh(assistant_turn)
                for item in created:
                    await db.refresh(item)
                if content.strip():
                    await _emit(
                        run.id,
                        "assistant_message",
                        {
                            "message_id": assistant_turn.id,
                            "round": round_index,
                            "content": content,
                        },
                    )

                for item in created:
                    if item.requires_approval:

                        async def approval_event(event_type: str, payload: dict[str, Any]) -> None:
                            await _emit(run.id, event_type, payload)

                        try:
                            summary = await build_approval_summary(
                                item.tool_name,
                                item.arguments,
                                ToolContext(
                                    db=db,
                                    user=user,
                                    server=server,
                                    emit=approval_event,
                                ),
                            )
                        except Exception as exc:
                            safe_error = redact_sensitive_text(str(exc), limit=2000)
                            audit_security_event(
                                "approval_plan_rejected",
                                user_id=user.id,
                                server_id=server.id if server is not None else None,
                                operation=item.tool_name,
                                detail=safe_error,
                            )
                            item.status = "failed"
                            item.error = safe_error
                            item.result = {"success": False, "error": safe_error}
                            item.completed_at = get_current_time()
                            db.add(item)
                            db.add(
                                AIMessage(
                                    conversation_id=run.conversation_id,
                                    role="tool",
                                    content=json.dumps(item.result, ensure_ascii=False),
                                    tool_call_id=item.tool_call_id,
                                    tool_name=item.tool_name,
                                    visible=False,
                                )
                            )
                            await db.commit()
                            await _emit(
                                run.id,
                                "tool_failed",
                                {
                                    "tool_run_id": item.id,
                                    "tool_name": item.tool_name,
                                    "result": item.result,
                                },
                            )
                            continue
                        item.plan_snapshot, item.progress_snapshot = _build_plan_snapshots(
                            item.tool_name, summary
                        )
                        item.progress_updated_at = get_current_time()
                        db.add(item)
                        await db.commit()
                        await _emit(
                            run.id,
                            "tool_approval_required",
                            {
                                "tool_run_id": item.id,
                                "tool_name": item.tool_name,
                                "arguments": sanitize_tool_result(item.arguments),
                                "arguments_hash": item.arguments_hash,
                                "risk": item.risk,
                                "summary": sanitize_tool_result(summary),
                            },
                        )
                    else:
                        await _execute_tool_run(db, run, item, user, server)

                if any(item.status == "pending_approval" for item in created):
                    run.status = "waiting_approval"
                    db.add(run)
                    await db.commit()
                    await _emit(run.id, "run_waiting_approval", {"status": run.status})
                    return

            raise AIProviderError(
                f"The assistant reached the maximum number of reasoning rounds ({max_rounds}). Send another message to continue with the remaining tasks."
            )
        except Exception as exc:
            logger.warning("AI run %s failed: %s", run.id, exc)
            await _fail_run(db, run, str(exc))


async def interrupt_active_ai_runs() -> int:
    """Mark non-terminal work interrupted after an application restart."""
    async with async_session_maker() as db:
        result = await db.execute(select(AIRun).where(AIRun.status.in_(ACTIVE_RUN_STATUSES)))
        runs = list(result.scalars().all())
        user_ids = {run.user_id for run in runs}
        if runs:
            tool_result = await db.execute(
                select(AIToolRun).where(
                    AIToolRun.run_id.in_([run.id for run in runs]),
                    AIToolRun.status.in_(
                        ("pending", "pending_approval", "approved", "queued", "running")
                    ),
                )
            )
            tools_by_run: dict[str, list[AIToolRun]] = {}
            for item in tool_result.scalars().all():
                tools_by_run.setdefault(item.run_id, []).append(item)
            for run in runs:
                await _close_unexecuted_tools(
                    db,
                    run,
                    tools_by_run.get(run.id, []),
                    expired_ids=set(),
                    cancellation_error="Application restarted before this tool completed",
                    cancellation_status="interrupted",
                )
        for run in runs:
            run.status = "interrupted"
            run.error = "Application restarted before this run completed"
            run.completed_at = get_current_time()
            db.add(run)
            _add_run_error_message(db, run, run.error)
        await db.commit()
    # Release stale AI write locks left behind by the previous process.
    for uid in user_ids:
        key = f"server_operation_lock:-{uid + 1}"
        try:
            await redis_manager.client.delete(key)
        except Exception:
            pass
    return len(runs)


async def interrupt_conversation_run(
    db: AsyncSession, user: User, conversation_id: str
) -> dict[str, Any]:
    """Force-stop the active AI run for a conversation so the user can send a new message."""
    result = await db.execute(
        select(AIRun).where(
            AIRun.conversation_id == conversation_id,
            AIRun.status.in_(ACTIVE_RUN_STATUSES),
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        return {"interrupted": False, "message": "No active run found for this conversation"}
    if run.user_id != user.id and not user.is_admin:
        raise PermissionError("Only the conversation owner or an admin can interrupt this run")
    if run.status == "waiting_approval":
        tool_result = await db.execute(
            select(AIToolRun).where(
                AIToolRun.run_id == run.id,
                AIToolRun.status.in_(("pending_approval", "approved", "queued")),
            )
        )
        await _close_unexecuted_tools(
            db,
            run,
            list(tool_result.scalars().all()),
            expired_ids=set(),
            cancellation_error="Interrupted by user before approval execution",
        )
    run.status = "interrupted"
    run.error = "Interrupted by user"
    run.completed_at = get_current_time()
    db.add(run)
    _add_run_error_message(db, run, run.error)
    await db.commit()
    await _emit(run.id, "run_interrupted", {"error": "Interrupted by user"})
    return {"interrupted": True, "run_id": run.id}
