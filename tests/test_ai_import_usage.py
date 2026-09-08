"""Import telemetry only publishes counts, including buffered reasoning deltas."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from modules.models import PluginImportJob
from modules.plugin_ai import ImportTokenUsage
from services.ai.progress import StreamProgress
from services.ai.streaming import consume_chat_completion_stream, consume_responses_stream
from services.plugins.ai_import_store import append_event, snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["chat", "responses"])
async def test_reasoning_counts_without_exposing_text(protocol):
    callback = AsyncMock()
    progress = StreamProgress(callback, {"messages": [{"content": "input"}]})
    await progress.emit(force=True)
    if protocol == "chat":
        events = [
            {"choices": [{"delta": {"reasoning_content": "private thought"}}]},
            {"choices": [{"delta": {"content": "answer"}, "finish_reason": "stop"}]},
            {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 30,
                    "completion_tokens_details": {"reasoning_tokens": 20},
                }
            },
        ]
        consume = consume_chat_completion_stream
    else:
        events = [
            {"type": "response.reasoning_text.delta", "delta": "private thought"},
            {"type": "response.output_text.delta", "delta": "answer"},
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": "answer"}]}
                    ],
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 30,
                        "output_tokens_details": {"reasoning_tokens": 20},
                    },
                },
            },
        ]
        consume = consume_responses_stream
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text="".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n",
    )
    message = await consume(response, None, progress)
    await progress.finish(message)
    values = [call.args[0] for call in callback.call_args_list]
    assert values[0]["stage"] == "waiting"
    thinking = next(value for value in values if value["stage"] == "thinking")
    assert thinking["reasoning_tokens"] > 0
    assert thinking["output_tokens"] == thinking["reasoning_tokens"]
    assert values[-1] == dict(
        input_tokens=100, output_tokens=30, reasoning_tokens=20, estimated=False, stage="completed"
    )
    assert "private thought" not in json.dumps(values)
    assert "private thought" not in json.dumps(message)


@pytest.mark.asyncio
async def test_progress_throttles_and_new_attempt_resets_counts(monkeypatch):
    monkeypatch.setattr("services.ai.progress.time.monotonic", lambda: 100)
    callback = AsyncMock()
    progress = StreamProgress(callback, {})
    await progress.emit(force=True)
    for _ in range(100):
        await progress.chat({"choices": [{"delta": {"reasoning": "hidden"}}]})
    assert callback.await_count == 2  # start, stage change; no per-chunk database writes
    await progress.finish({})
    assert callback.call_args.args[0]["reasoning_tokens"] == 150
    restarted = StreamProgress(callback, {})
    await restarted.emit(force=True)
    assert callback.call_args.args[0]["output_tokens"] == 0


def test_counter_events_coalesce_and_preserve_job_phase_and_history():
    job = PluginImportJob(
        actor_user_id=1,
        request_key="test",
        options={},
        command="import",
        created_at=datetime.now(timezone.utc),
    )
    append_event(job, "reading", "Read documentation", "https://github.com/test/plugin")
    usage = ImportTokenUsage(
        input_tokens=100, output_tokens=10, reasoning_tokens=5, estimated=True, stage="thinking"
    )
    for _ in range(350):
        append_event(job, "token_usage", "usage", token_usage=usage)
    assert len(job.events) == 2
    assert job.events[-1]["sequence"] == 351
    assert job.phase == "reading"
    assert job.current_repository == "https://github.com/test/plugin"
    assert snapshot(job).events[-1].token_usage == usage
    append_event(job, "analyzing", "Retry 2/10")
    append_event(job, "token_usage", "usage", token_usage=usage)
    assert len(job.events) == 4
    assert job.message == "Retry 2/10"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["truncated", "json_error", "html"])
async def test_buffered_provider_retry_publishes_fresh_numeric_attempt(monkeypatch, failure):
    from services import ai_provider, http_retry
    from services.ai_security import AIProviderConfig
    from services.http_retry import BackgroundRetry

    config = AIProviderConfig(
        base_url="https://provider.example/v1",
        model="test",
        api_key="secret",
        timeout_seconds=10,
        allowlist=(),
        source="global",
    )
    monkeypatch.setattr(
        ai_provider, "validate_provider_endpoint", AsyncMock(return_value=config.base_url)
    )
    monkeypatch.setattr(http_retry.asyncio, "sleep", AsyncMock())
    callback = AsyncMock()
    calls = 0

    def handle(_request):
        nonlocal calls
        calls += 1
        if calls == 1 and failure == "json_error":
            return httpx.Response(200, json={"error": {"type": "server_error"}})
        if calls == 1 and failure == "html":
            return httpx.Response(200, text="<html>gateway unavailable</html>")
        text = 'data: {"choices":[{"delta":{"reasoning_content":"private thought"}}]}\n\n'
        if calls > 1:
            text += 'data: {"choices":[{"delta":{"content":"success"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=text)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        ai_provider.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(**{"transport": httpx.MockTransport(handle), **kwargs}),
    )
    retry = BackgroundRetry(AsyncMock(), AsyncMock())
    try:
        result = await ai_provider.create_chat_completion(
            config,
            [{"role": "user", "content": "test"}],
            stream=True,
            retry=retry,
            on_progress=callback,
        )
    finally:
        await ai_provider.ai_provider_transport.close()
    assert result["content"] == "success"
    assert calls == 2
    retry.notify.assert_awaited_once()
    values = [call.args[0] for call in callback.call_args_list]
    starts = [value for value in values if value["stage"] == "waiting"]
    assert len(starts) == 2 and all(value["output_tokens"] == 0 for value in starts)
    assert "private thought" not in json.dumps(values)


def test_partial_provider_usage_does_not_mark_later_estimates_as_reported():
    progress = StreamProgress(AsyncMock(), {})
    progress.usage({"usage": {"input_tokens": 100, "output_tokens": 10}})
    assert not progress.value["estimated"]
    progress.add_text("new streamed text")
    assert progress.value["estimated"]
