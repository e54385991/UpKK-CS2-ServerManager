"""Security and compatibility coverage for the AI assistant foundation."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from services import ai_provider
from services.ai_provider import (
    create_chat_completion,
)
from services.ai_security import (
    AIProviderConfig,
)
from tests.ai_assistant_security_fakes import (
    _sse_response as _sse_response,
)


@pytest.mark.asyncio
async def test_responses_protocol_maps_history_tools_and_output(monkeypatch):
    captured: dict = {}
    requested_url = ""
    original_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested_url
        requested_url = str(request.url)
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_123",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Done"}],
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_next",
                        "name": "probe",
                        "arguments": '{"value":2}',
                    },
                ],
                "usage": {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16},
            },
        )

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example/v1"),
    )
    config = AIProviderConfig(
        base_url="https://provider.example/v1",
        model="responses-model",
        api_key=None,
        timeout_seconds=10,
        allowlist=(),
        source="global",
        api_protocol="responses",
        reasoning_effort="high",
        verbosity="low",
        max_completion_tokens=4096,
        token_limit_parameter="max_tokens",
        parallel_tool_calls=False,
    )
    message = await create_chat_completion(
        config,
        [
            {"role": "user", "content": "inspect"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_old",
                        "type": "function",
                        "function": {"name": "probe", "arguments": '{"value":1}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_old", "content": '{"ok":true}'},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "probe",
                    "description": "Probe a value",
                    "parameters": {
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                    },
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": "probe"}},
    )

    assert requested_url == "https://provider.example/v1/responses"
    assert "messages" not in captured
    assert captured["store"] is False
    assert captured["max_output_tokens"] == 4096
    assert captured["reasoning"] == {"effort": "high"}
    assert captured["text"] == {"verbosity": "low"}
    assert captured["parallel_tool_calls"] is False
    assert captured["input"][1] == {
        "type": "function_call",
        "call_id": "call_old",
        "name": "probe",
        "arguments": '{"value":1}',
    }
    assert captured["input"][2] == {
        "type": "function_call_output",
        "call_id": "call_old",
        "output": '{"ok":true}',
    }
    assert captured["tools"][0]["name"] == "probe"
    assert captured["tools"][0]["strict"] is False
    assert "function" not in captured["tools"][0]
    assert captured["tool_choice"] == {"type": "function", "name": "probe"}
    assert message["content"] == "Done"
    assert message["tool_calls"] == [
        {
            "id": "call_next",
            "type": "function",
            "function": {"name": "probe", "arguments": '{"value":2}'},
        }
    ]
    assert message["usage"]["input_tokens"] == 12


@pytest.mark.asyncio
async def test_responses_protocol_streams_text_and_normalizes_final_tools(monkeypatch):
    original_client = httpx.AsyncClient

    def handler(_request: httpx.Request) -> httpx.Response:
        return _sse_response(
            {"type": "response.created", "response": {"id": "resp_stream"}},
            {"type": "response.output_text.delta", "delta": "# Status"},
            {"type": "response.output_text.delta", "delta": "\nRunning"},
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_stream",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "# Status\nRunning"}],
                        },
                        {
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "probe",
                            "arguments": "{}",
                        },
                    ],
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                },
            },
        )

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example/v1"),
    )
    deltas: list[str] = []

    async def receive_delta(delta: str) -> None:
        deltas.append(delta)

    message = await create_chat_completion(
        AIProviderConfig(
            base_url="https://provider.example/v1",
            model="responses-model",
            api_key=None,
            timeout_seconds=10,
            allowlist=(),
            source="global",
            api_protocol="responses",
        ),
        [{"role": "user", "content": "status"}],
        stream=True,
        on_text_delta=receive_delta,
    )

    assert deltas == ["# Status", "\nRunning"]
    assert message["content"] == "# Status\nRunning"
    assert message["tool_calls"][0]["id"] == "call_1"
    assert message["usage"] == {"input_tokens": 3, "output_tokens": 2}
