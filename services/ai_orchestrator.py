"""Bounded OpenAI Chat Completions tool-calling orchestration."""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func
from sqlmodel import select

from modules.database import async_session_maker
from modules.models import (
    AIConversation,
    AIMessage,
    AIRun,
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

logger = logging.getLogger(__name__)
MAX_PROVIDER_ROUNDS = 8
MAX_TOOL_CALLS_PER_ROUND = 5
MAX_REPEATED_CALLS = 3
ACTIVE_RUN_STATUSES = ("queued", "running", "waiting_approval")
AI_DELTA_EVENT_CHARS = 96
AI_WRITE_QUEUE_WAIT_SECONDS = 30 * 60


async def _emit(run_id: str, event_type: str, payload: dict[str, Any]) -> None:
    await ai_event_hub.emit(run_id, event_type, payload)


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


async def _fail_run(db, run: AIRun, message: str) -> None:
    safe_message = redact_sensitive_text(message, limit=2000)
    run.status = "failed"
    run.error = safe_message
    run.completed_at = get_current_time()
    db.add(run)
    await db.commit()
    await _emit(run.id, "run_failed", {"error": safe_message})


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
        await _emit(
            run.id,
            event_type,
            {"tool_run_id": tool_run.id, "tool_name": tool_run.tool_name, **payload},
        )

    context = ToolContext(db=db, user=user, server=server, emit=tool_event)

    async def mark_running() -> None:
        tool_run.status = "running"
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
                ttl=30 * 60,
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
        event_type = "tool_failed"
    else:
        tool_run.status = "completed"
        tool_run.result = result
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

            while rounds_used < MAX_PROVIDER_ROUNDS:
                messages = await _load_provider_messages(
                    db, conversation, user, server, provider.admin_prompt
                )
                round_index = rounds_used + 1
                delta_emitter = _AssistantDeltaEmitter(run.id, round_index)
                try:
                    response = await create_chat_completion(
                        provider,
                        messages,
                        tools=tool_definitions(server_selected=server is not None),
                        stream=True,
                        on_text_delta=delta_emitter.add,
                    )
                finally:
                    await delta_emitter.flush()
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
                if not isinstance(calls, list) or len(calls) > MAX_TOOL_CALLS_PER_ROUND:
                    raise AIProviderError(
                        f"Tool-call limit exceeded ({MAX_TOOL_CALLS_PER_ROUND} per round)"
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

            raise AIProviderError(f"Provider round limit exceeded ({MAX_PROVIDER_ROUNDS})")
        except Exception as exc:
            logger.warning("AI run %s failed: %s", run.id, exc)
            await _fail_run(db, run, str(exc))


async def interrupt_active_ai_runs() -> int:
    """Mark non-terminal work interrupted after an application restart."""
    async with async_session_maker() as db:
        result = await db.execute(select(AIRun).where(AIRun.status.in_(ACTIVE_RUN_STATUSES)))
        runs = list(result.scalars().all())
        for run in runs:
            run.status = "interrupted"
            run.error = "Application restarted before this run completed"
            run.completed_at = get_current_time()
            db.add(run)
        await db.commit()
        return len(runs)
