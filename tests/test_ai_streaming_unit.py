"""Unit coverage for the bounded AI provider stream state machines."""

from __future__ import annotations

import json

import httpx
import pytest

from services.ai.errors import AIProviderError
from services.ai.streaming import (
    ChatStreamAccumulator,
    ResponsesStreamAccumulator,
    consume_chat_completion_stream,
    consume_responses_stream,
    iter_sse_data,
    normalize_responses_message,
    parse_sse_json,
)


def response(body: str, *, content_type: str = "text/event-stream") -> httpx.Response:
    return httpx.Response(200, headers={"content-type": content_type}, content=body.encode())


@pytest.mark.asyncio
async def test_sse_parser_joins_data_lines_and_ignores_comments():
    records = [
        record async for record in iter_sse_data(response(": keep\ndata: {\n" + 'data: "ok"\n\n'))
    ]
    assert records == ['{\n"ok"']


def test_sse_json_parser_rejects_invalid_payloads():
    with pytest.raises(AIProviderError, match="invalid JSON"):
        parse_sse_json("not-json", protocol="Chat Completions")
    with pytest.raises(AIProviderError, match="invalid Responses SSE event"):
        parse_sse_json("[]", protocol="Responses")


@pytest.mark.asyncio
async def test_chat_accumulator_reassembles_content_tools_and_usage():
    deltas: list[str] = []

    async def collect(value: str) -> None:
        deltas.append(value)

    accumulator = ChatStreamAccumulator()
    await accumulator.add(
        {
            "usage": {"total_tokens": 3},
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "content": "Hi",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call",
                                "function": {"name": "read", "arguments": "{"},
                            }
                        ],
                    }
                }
            ],
        },
        collect,
    )
    await accumulator.add(
        {
            "choices": [
                {
                    "delta": {
                        "content": "!",
                        "tool_calls": [{"index": 0, "function": {"arguments": "}"}}],
                    }
                }
            ]
        },
        collect,
    )
    message = accumulator.message()
    assert message["content"] == "Hi!"
    assert message["usage"] == {"total_tokens": 3}
    assert message["tool_calls"][0]["function"]["arguments"] == "{}"
    assert deltas == ["Hi", "!"]


@pytest.mark.asyncio
async def test_chat_accumulator_rejects_malformed_chunks_and_empty_streams():
    accumulator = ChatStreamAccumulator()
    with pytest.raises(AIProviderError, match="invalid streamed text"):
        await accumulator.add({"choices": [{"delta": {"content": 1}}]}, None)
    with pytest.raises(AIProviderError, match="invalid streamed tool calls"):
        await accumulator.add({"choices": [{"delta": {"tool_calls": {}}}]}, None)
    with pytest.raises(AIProviderError, match="invalid tool call index"):
        await accumulator.add({"choices": [{"delta": {"tool_calls": [{"index": 99}]}}]}, None)
    with pytest.raises(AIProviderError, match="invalid streamed function call"):
        await accumulator.add(
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": []}]}}]}, None
        )
    with pytest.raises(AIProviderError, match="empty SSE"):
        ChatStreamAccumulator().message()


@pytest.mark.asyncio
async def test_chat_stream_consumer_handles_errors_and_content_type():
    payload = json.dumps({"choices": [{"delta": {"content": "ok"}}]})
    assert (
        await consume_chat_completion_stream(response(f"data: {payload}\n\ndata: [DONE]\n\n"), None)
    )["content"] == "ok"
    with pytest.raises(AIProviderError, match="standard SSE"):
        await consume_chat_completion_stream(response("", content_type="application/json"), None)
    error = json.dumps({"error": "provider secret"})
    with pytest.raises(AIProviderError, match="SSE stream failed"):
        await consume_chat_completion_stream(response(f"data: {error}\n\n"), None)


def test_responses_normalizer_handles_text_refusal_and_tools():
    message = normalize_responses_message(
        {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "hello"},
                        {"type": "refusal", "refusal": "no"},
                        {"type": "ignored"},
                    ],
                },
                {"type": "function_call", "call_id": "c1", "name": "lookup", "arguments": "{}"},
                {"type": "other"},
            ],
            "usage": {"total_tokens": 2},
        }
    )
    assert message == {
        "role": "assistant",
        "content": "hellono",
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}
        ],
        "usage": {"total_tokens": 2},
    }


def test_responses_normalizer_rejects_bad_output_and_calls():
    with pytest.raises(AIProviderError, match="did not complete"):
        normalize_responses_message({"status": "failed", "error": "bad"})
    with pytest.raises(AIProviderError, match="invalid Responses output"):
        normalize_responses_message({"output": "bad"})
    with pytest.raises(AIProviderError, match="invalid Responses function call"):
        normalize_responses_message({"output": [{"type": "function_call", "name": "missing-id"}]})


@pytest.mark.asyncio
async def test_responses_accumulator_streams_and_requires_completion():
    deltas: list[str] = []

    async def collect(value: str) -> None:
        deltas.append(value)

    accumulator = ResponsesStreamAccumulator()
    await accumulator.add({"type": "response.output_text.delta", "delta": "hello"}, collect)
    await accumulator.add({"type": "response.completed", "response": {"output": []}}, collect)
    assert accumulator.message() == {"role": "assistant", "content": "hello"}
    assert deltas == ["hello"]

    with pytest.raises(AIProviderError, match="invalid streamed text"):
        await ResponsesStreamAccumulator().add(
            {"type": "response.output_text.delta", "delta": None}, None
        )
    with pytest.raises(AIProviderError, match="invalid completed response"):
        await ResponsesStreamAccumulator().add({"type": "response.completed", "response": []}, None)
    with pytest.raises(AIProviderError, match="stream failed"):
        await ResponsesStreamAccumulator().add({"type": "error", "error": "bad"}, None)
    with pytest.raises(AIProviderError, match="empty SSE"):
        ResponsesStreamAccumulator().message()
    incomplete = ResponsesStreamAccumulator()
    await incomplete.add({"type": "response.output_text.delta", "delta": "x"}, None)
    with pytest.raises(AIProviderError, match="before completion"):
        incomplete.message()


@pytest.mark.asyncio
async def test_responses_stream_consumer_requires_sse_and_completion():
    body = (
        'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
        'data: {"type":"response.completed","response":{"output":[]}}\n\n'
        "data: [DONE]\n\n"
    )
    assert (await consume_responses_stream(response(body), None))["content"] == "ok"
    with pytest.raises(AIProviderError, match="standard SSE"):
        await consume_responses_stream(response("", content_type="application/json"), None)
