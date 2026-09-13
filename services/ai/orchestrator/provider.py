"""Provider HTTP retries for AI chat completions."""

from __future__ import annotations

from typing import Any

from modules.models import AIRun
from services.compat import LateBoundModule

host = LateBoundModule("services.ai_orchestrator")


def _retry_delay_seconds(retry_attempt: int) -> int:
    return host.AI_RETRY_BASE_SECONDS * (2 ** (retry_attempt - 1))


async def _create_provider_response_with_retry(
    provider,
    messages: list[dict[str, Any]],
    *,
    run_id: str,
    round_index: int,
    server_selected: bool,
    allowed_capabilities=None,
    estimated_input_tokens: int = 0,
) -> dict[str, Any]:
    for retry_attempt in range(host.AI_RETRY_MAX_ATTEMPTS + 1):
        delta_emitter = host._AssistantDeltaEmitter(run_id, round_index, estimated_input_tokens)
        try:
            response = await host.create_chat_completion(
                provider,
                messages,
                tools=host.tool_definitions(
                    server_selected=server_selected,
                    allowed_capabilities=allowed_capabilities,
                ),
                stream=True,
                on_text_delta=delta_emitter.add,
            )
            response_content = str(response.get("content") or "")
            if not response.get("tool_calls") and not response_content.strip():
                raise host.AIProviderError("AI provider returned neither text nor tool calls")
            await delta_emitter.flush()
        except host.AIProviderError as exc:
            delta_emitter.buffer = ""
            if isinstance(exc, host.AIPayloadTooLargeError):
                raise
            if retry_attempt >= host.AI_RETRY_MAX_ATTEMPTS:
                raise
            next_attempt = retry_attempt + 1
            delay = _retry_delay_seconds(next_attempt)
            safe_error = host.redact_sensitive_text(str(exc), limit=2000)
            await host._emit(
                run_id,
                "run_retrying",
                {
                    "round": round_index,
                    "attempt": next_attempt,
                    "max_attempts": host.AI_RETRY_MAX_ATTEMPTS,
                    "delay_seconds": delay,
                    "error": safe_error,
                },
            )
            await host.asyncio.sleep(delay)
            continue
        return response
    raise host.AIProviderError("AI provider retry loop ended unexpectedly")


async def _fail_run(db, run: AIRun, message: str) -> None:
    safe_message = host.redact_sensitive_text(message, limit=2000)
    run.status = "failed"
    run.error = safe_message
    run.completed_at = host.get_current_time()
    db.add(run)
    host._add_run_error_message(db, run, safe_message)
    await db.commit()
    await host._emit(run.id, "run_failed", {"error": safe_message})
