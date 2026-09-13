"""Process a persisted AI run through provider rounds and tool calls."""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func
from sqlmodel import select

from modules.models import (
    AIConversation,
    AIMessage,
    AIRun,
    AIToolRun,
    User,
)
from services.compat import LateBoundModule

host = LateBoundModule("services.ai_orchestrator")
logger = logging.getLogger(__name__)


async def process_ai_run(run_id: str) -> None:  # noqa: C901 - orchestration state machine.
    """Run or resume one conversation job. Exceptions become persisted failures."""
    async with host.async_session_maker() as db:
        run = await db.get(AIRun, run_id)
        if run is None or run.status not in host.ACTIVE_RUN_STATUSES:
            return
        conversation = await db.get(AIConversation, run.conversation_id)
        user = await db.get(User, run.user_id)
        if conversation is None or user is None or conversation.user_id != user.id:
            await host._fail_run(db, run, "Conversation owner is unavailable")
            return
        server = None
        if run.server_id is not None:
            server = (
                await host.Server.get_by_id(db, run.server_id)
                if user.is_admin
                else await host.Server.get_by_id_and_user(db, run.server_id, user.id)
            )
            if server is None:
                await host._fail_run(db, run, "Selected server is no longer available")
                return
        try:
            provider = await host.get_effective_provider(db, user)
        except host.AIConfigurationError as exc:
            await host._fail_run(db, run, str(exc))
            return
        if provider is None:
            await host._fail_run(db, run, "No AI provider is enabled")
            return

        settings = await host.AISystemSettings.get_or_create(db)
        max_rounds = min(
            max(
                int(
                    getattr(settings, "max_provider_rounds", 0) or host.DEFAULT_MAX_PROVIDER_ROUNDS
                ),
                1,
            ),
            host.MAX_CONFIGURED_AI_LIMIT,
        )
        max_tool_calls_per_round = min(
            max(
                int(
                    getattr(settings, "max_tool_calls_per_round", 0)
                    or host.DEFAULT_MAX_TOOL_CALLS_PER_ROUND
                ),
                1,
            ),
            host.MAX_CONFIGURED_AI_LIMIT,
        )

        run.status = "running"
        run.error = None
        db.add(run)
        await db.commit()
        await host._emit(run.id, "run_started", {"status": run.status})

        try:
            if await host._resume_decided_tools(db, run, user, server):
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
            existing_tool_runs = list(tool_result.scalars().all())
            signatures = Counter(
                (item.tool_name, item.arguments_hash) for item in existing_tool_runs
            )
            previous_results = {
                (item.tool_name, item.arguments_hash): item.result
                for item in existing_tool_runs
                if item.result is not None
            }
            session_input_tokens = 0
            session_output_tokens = 0
            session_cached_tokens = 0

            while rounds_used < max_rounds:
                await db.refresh(run)
                if run.status == "interrupted":
                    await host._emit(
                        run.id, "run_interrupted", {"error": run.error or "Interrupted"}
                    )
                    return
                messages = await host._load_provider_messages(
                    db, conversation, user, server, provider.admin_prompt, provider
                )
                round_index = rounds_used + 1
                estimated_input_tokens = host._estimate_message_tokens(messages)
                await host._emit(
                    run.id,
                    "token_usage",
                    {
                        "round": round_index,
                        "input_tokens": session_input_tokens + estimated_input_tokens,
                        "output_tokens": session_output_tokens,
                        "total_tokens": session_input_tokens
                        + session_output_tokens
                        + estimated_input_tokens,
                        "estimated": True,
                    },
                )
                allowed_capabilities = None
                if server is not None and server.id is not None:
                    effective_policy = await host.get_effective_agent_policy(db, server.id)
                    if not effective_policy.enabled:
                        raise host.AgentCapabilityDenied("AI Agent is disabled for this server")
                    allowed_capabilities = effective_policy.capabilities
                response = await host._create_provider_response_with_retry(
                    provider,
                    messages,
                    run_id=run.id,
                    round_index=round_index,
                    server_selected=server is not None,
                    allowed_capabilities=allowed_capabilities,
                    estimated_input_tokens=estimated_input_tokens,
                )
                rounds_used += 1
                provider_usage = host._provider_token_usage(response)
                if provider_usage is None:
                    session_input_tokens += estimated_input_tokens
                    session_output_tokens += host._estimate_response_tokens(response)
                    usage_estimated = True
                else:
                    session_input_tokens += provider_usage[0]
                    session_output_tokens += provider_usage[1]
                    session_cached_tokens += provider_usage[2]
                    usage_estimated = False
                await host._emit(
                    run.id,
                    "token_usage",
                    {
                        "round": round_index,
                        "input_tokens": session_input_tokens,
                        "output_tokens": session_output_tokens,
                        "total_tokens": session_input_tokens + session_output_tokens,
                        # Already included in input_tokens; shown so an operator
                        # can see the upstream prompt cache working.
                        "cached_input_tokens": session_cached_tokens,
                        "estimated": usage_estimated,
                    },
                )
                content = host.redact_sensitive_text(
                    str(response.get("content") or ""), limit=20_000
                )
                calls = response.get("tool_calls")
                if not calls:
                    if not content.strip():
                        raise host.AIProviderError(
                            "AI provider returned neither text nor tool calls"
                        )
                    assistant = AIMessage(
                        conversation_id=conversation.id,
                        role="assistant",
                        content=content,
                        visible=True,
                    )
                    db.add(assistant)
                    conversation.updated_at = host.get_current_time()
                    run.status = "completed"
                    run.completed_at = host.get_current_time()
                    db.add(conversation)
                    db.add(run)
                    await db.commit()
                    await db.refresh(assistant)
                    await host._emit(
                        run.id,
                        "assistant_message",
                        {
                            "message_id": assistant.id,
                            "round": round_index,
                            "content": content,
                        },
                    )
                    await host._emit(run.id, "run_completed", {"status": run.status})
                    return
                if not isinstance(calls, list) or len(calls) > max_tool_calls_per_round:
                    raise host.AIProviderError(
                        f"Tool-call limit exceeded ({max_tool_calls_per_round} per round)"
                    )

                normalized_calls: list[tuple[dict[str, Any], str, dict[str, Any], str]] = []
                duplicate_read_calls: dict[str, tuple[str, str]] = {}
                seen_ids: set[str] = set()
                for raw_call in calls:
                    if not isinstance(raw_call, dict):
                        raise host.AIProviderError("AI provider returned an invalid tool call")
                    call_id = str(raw_call.get("id") or "")
                    function = raw_call.get("function")
                    if (
                        not call_id
                        or len(call_id) > 100
                        or call_id in seen_ids
                        or not isinstance(function, dict)
                    ):
                        raise host.AIProviderError("AI provider returned an invalid tool call ID")
                    seen_ids.add(call_id)
                    name = str(function.get("name") or "")
                    spec = host.TOOLS_BY_NAME.get(name)
                    if spec is None:
                        raise host.AIProviderError(f"AI provider requested unknown tool: {name}")
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise host.AIProviderError(
                            f"AI provider supplied invalid JSON for {name}"
                        ) from exc
                    if not isinstance(arguments, dict):
                        raise host.AIProviderError(f"Tool arguments for {name} must be an object")
                    try:
                        validated = spec.input_model.model_validate(arguments)
                    except ValidationError as exc:
                        raise host.AIProviderError(
                            f"Invalid arguments for {name}: {exc.errors(include_url=False)}"
                        ) from exc
                    clean_arguments = validated.model_dump(mode="json")
                    if spec.requires_server and server is not None and server.id is not None:
                        await host.require_agent_capabilities(
                            db,
                            server.id,
                            spec.required_capabilities(clean_arguments),
                        )
                    _, arguments_hash = host.canonical_arguments(clean_arguments)
                    signatures[(name, arguments_hash)] += 1
                    if signatures[(name, arguments_hash)] > host.MAX_REPEATED_CALLS:
                        if spec.risk != "read":
                            raise host.AIProviderError(
                                f"Repeated tool-call loop detected for {name}"
                            )
                        duplicate_read_calls[call_id] = (name, arguments_hash)
                    normalized_calls.append((raw_call, name, clean_arguments, arguments_hash))

                host._validate_write_tool_batch([item[1] for item in normalized_calls])

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
                    spec = host.TOOLS_BY_NAME[name]
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
                            host.get_current_time() + timedelta(minutes=15)
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
                    await host._emit(
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
                            await host._emit(run.id, event_type, payload)

                        try:
                            summary = await host.build_approval_summary(
                                item.tool_name,
                                item.arguments,
                                host.ToolContext(
                                    db=db,
                                    user=user,
                                    server=server,
                                    emit=approval_event,
                                ),
                            )
                        except Exception as exc:
                            safe_error = host.redact_sensitive_text(str(exc), limit=2000)
                            host.audit_security_event(
                                "approval_plan_rejected",
                                user_id=user.id,
                                server_id=server.id if server is not None else None,
                                operation=item.tool_name,
                                detail=safe_error,
                            )
                            item.status = "failed"
                            item.error = safe_error
                            item.result = {"success": False, "error": safe_error}
                            item.completed_at = host.get_current_time()
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
                            await host._emit(
                                run.id,
                                "tool_failed",
                                {
                                    "tool_run_id": item.id,
                                    "tool_name": item.tool_name,
                                    "result": item.result,
                                },
                            )
                            continue
                        item.plan_snapshot, item.progress_snapshot = host._build_plan_snapshots(
                            item.tool_name, summary
                        )
                        item.progress_updated_at = host.get_current_time()
                        db.add(item)
                        await db.commit()
                        await host._emit(
                            run.id,
                            "tool_approval_required",
                            {
                                "tool_run_id": item.id,
                                "tool_name": item.tool_name,
                                "arguments": host.sanitize_tool_result(item.arguments),
                                "arguments_hash": item.arguments_hash,
                                "risk": item.risk,
                                "summary": host.sanitize_tool_result(summary),
                            },
                        )
                    else:
                        signature = duplicate_read_calls.get(item.tool_call_id)
                        if signature is None:
                            await host._execute_tool_run(db, run, item, user, server)
                            if isinstance(item.result, dict):
                                previous_results[(item.tool_name, item.arguments_hash)] = (
                                    item.result
                                )
                            continue
                        reused_result = {
                            "success": True,
                            "duplicate_call": True,
                            "message": (
                                "This identical read-only tool call was already completed. "
                                "Use the previous result, change the search arguments, or answer "
                                "the user; do not repeat the same call again."
                            ),
                            "previous_result": host.sanitize_tool_result(
                                previous_results.get(signature)
                            ),
                        }
                        await host._execute_tool_run(
                            db,
                            run,
                            item,
                            user,
                            server,
                            precomputed_result=reused_result,
                        )

                if any(item.status == "pending_approval" for item in created):
                    run.status = "waiting_approval"
                    db.add(run)
                    await db.commit()
                    await host._emit(run.id, "run_waiting_approval", {"status": run.status})
                    return

            raise host.AIProviderError(
                f"The assistant reached the maximum number of reasoning rounds ({max_rounds}). Send another message to continue with the remaining tasks."
            )
        except Exception as exc:
            logger.warning("AI run %s failed: %s", run.id, exc)
            await host._fail_run(db, run, str(exc))
