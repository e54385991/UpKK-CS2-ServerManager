"""Security and compatibility coverage for the AI assistant foundation."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from services import ai_provider
from services.ai_provider import (
    MAX_PROVIDER_REQUEST_BYTES,
    AIPayloadTooLargeError,
    AIProviderError,
    create_chat_completion,
)
from services.ai_provider import (
    test_provider as probe_provider,
)
from services.ai_security import (
    AIProviderConfig,
)
from tests.ai_assistant_security_fakes import (
    _sse_response as _sse_response,
)


@pytest.mark.asyncio
async def test_standard_chat_completions_tool_call_probe(monkeypatch):
    original_client = httpx.AsyncClient
    client_creations = 0

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert payload["stream_options"] == {"include_usage": True}
        if payload.get("tools"):
            nonce = payload["messages"][0]["content"].rsplit(" ", 1)[-1].rstrip(".")
            arguments = json.dumps({"nonce": nonce})
            return _sse_response(
                {
                    "choices": [
                        {
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "probe",
                                        "type": "function",
                                        "function": {
                                            "name": "ai_capability_probe",
                                            "arguments": arguments[:8],
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": arguments[8:]}}
                                ]
                            }
                        }
                    ]
                },
            )
        return _sse_response(
            {"choices": [{"delta": {"role": "assistant", "content": "O"}}]},
            {"choices": [{"delta": {"content": "K"}}]},
        )

    def client_factory(**kwargs):
        nonlocal client_creations
        client_creations += 1
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example/v1"),
    )
    config = AIProviderConfig(
        base_url="https://provider.example/v1",
        model="test-model",
        api_key="secret",
        timeout_seconds=10,
        allowlist=(),
        source="global",
    )

    assert await probe_provider(config) == (
        True,
        True,
        True,
        "Provider SSE text and streamed tool-calling tests passed",
    )
    assert client_creations == 1


@pytest.mark.asyncio
async def test_streaming_completion_emits_markdown_deltas(monkeypatch):
    original_client = httpx.AsyncClient
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return _sse_response(
            {"choices": [{"delta": {"role": "assistant", "content": "# Status"}}]},
            {"choices": [{"delta": {"content": "\n\nRunning"}}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
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
    deltas = []

    async def receive_delta(delta: str) -> None:
        deltas.append(delta)

    message = await create_chat_completion(
        AIProviderConfig(
            base_url="https://provider.example/v1",
            model="test-model",
            api_key=None,
            timeout_seconds=10,
            allowlist=(),
            source="global",
        ),
        [{"role": "user", "content": "status"}],
        stream=True,
        on_text_delta=receive_delta,
    )

    assert captured["stream"] is True
    assert deltas == ["# Status", "\n\nRunning"]
    assert message["content"] == "# Status\n\nRunning"
    assert message["usage"] == {
        "prompt_tokens": 7,
        "completion_tokens": 5,
        "total_tokens": 12,
    }


@pytest.mark.asyncio
async def test_provider_refuses_redirects(monkeypatch):
    original_client = httpx.AsyncClient

    def client_factory(**kwargs):
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(302, headers={"location": "https://internal/"})
        )
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example/v1"),
    )
    config = AIProviderConfig(
        base_url="https://provider.example/v1",
        model="test-model",
        api_key=None,
        timeout_seconds=10,
        allowlist=(),
        source="global",
    )
    with pytest.raises(AIProviderError, match="redirect"):
        await create_chat_completion(config, [{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_provider_sends_validated_optional_model_parameters(monkeypatch):
    captured = {}
    original_client = httpx.AsyncClient

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
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
        model="reasoning-model",
        api_key=None,
        timeout_seconds=10,
        allowlist=(),
        source="global",
        reasoning_effort="xhigh",
        temperature=0,
        max_completion_tokens=4096,
        token_limit_parameter="max_completion_tokens",
        frequency_penalty=0.3,
        presence_penalty=-0.2,
        verbosity="high",
        parallel_tool_calls=False,
    )

    await create_chat_completion(
        config,
        [{"role": "user", "content": "hello"}],
        tools=[{"type": "function", "function": {"name": "probe", "parameters": {}}}],
    )

    assert captured["reasoning_effort"] == "xhigh"
    assert captured["temperature"] == 0
    assert captured["max_completion_tokens"] == 4096
    assert captured["frequency_penalty"] == 0.3
    assert captured["presence_penalty"] == -0.2
    assert captured["verbosity"] == "high"
    assert captured["parallel_tool_calls"] is False
    assert "top_p" not in captured
    assert "max_tokens" not in captured


@pytest.mark.asyncio
async def test_provider_compacts_oversized_history_before_sending(monkeypatch):
    original_client = httpx.AsyncClient
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        )

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example/v1"),
    )
    messages = [{"role": "system", "content": "Follow the system rules."}]
    messages.extend(
        {"role": "user", "content": f"old-{index} " + "x" * 10_000} for index in range(12)
    )
    messages.append({"role": "user", "content": "latest request"})

    await create_chat_completion(
        AIProviderConfig(
            base_url="https://provider.example/v1",
            model="test-model",
            api_key=None,
            timeout_seconds=10,
            allowlist=(),
            source="global",
        ),
        messages,
        tools=[{"type": "function", "function": {"name": "probe", "parameters": {}}}],
    )

    assert len(json.dumps(captured, ensure_ascii=False, separators=(",", ":")).encode()) <= (
        MAX_PROVIDER_REQUEST_BYTES
    )
    assert captured["messages"][-1]["content"] == "latest request"
    assert all("old-0" not in str(message) for message in captured["messages"])


@pytest.mark.asyncio
async def test_provider_root_origin_falls_back_to_conventional_v1_api(monkeypatch):
    original_client = httpx.AsyncClient
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/chat/completions":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"console")
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        )

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example"),
    )

    message = await create_chat_completion(
        AIProviderConfig(
            base_url="https://provider.example",
            model="test-model",
            api_key=None,
            timeout_seconds=10,
            allowlist=(),
            source="global",
        ),
        [{"role": "user", "content": "hello"}],
    )

    assert message["content"] == "OK"
    assert requested_paths == ["/chat/completions", "/v1/chat/completions"]


@pytest.mark.asyncio
async def test_provider_compacts_multiple_large_tool_outputs_to_the_request_budget(monkeypatch):
    original_client = httpx.AsyncClient
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        )

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example/v1"),
    )
    messages = [{"role": "system", "content": "诊断规则"}]
    messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": f"call-{index}",
                    "type": "function",
                    "function": {"name": "probe", "arguments": "{}"},
                }
                for index in range(4)
            ],
        }
    )
    messages.extend(
        {
            "role": "tool",
            "tool_call_id": f"call-{index}",
            "content": "日志输出 " + ("错误信息。" * 20_000),
        }
        for index in range(4)
    )

    await create_chat_completion(
        AIProviderConfig(
            base_url="https://provider.example/v1",
            model="test-model",
            api_key=None,
            timeout_seconds=10,
            allowlist=(),
            source="global",
        ),
        messages,
        tools=[{"type": "function", "function": {"name": "probe", "parameters": {}}}],
    )

    payload_size = len(json.dumps(captured, ensure_ascii=False, separators=(",", ":")).encode())
    assert payload_size <= MAX_PROVIDER_REQUEST_BYTES
    assert len(captured["messages"]) == len(messages)
    assert any(
        "earlier content truncated" in str(item.get("content")) for item in captured["messages"]
    )


@pytest.mark.asyncio
async def test_provider_413_is_classified_as_non_retryable_payload_error(monkeypatch):
    original_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return original_client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(413, json={"error": "too large"})
            ),
            **kwargs,
        )

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example/v1"),
    )

    with pytest.raises(AIPayloadTooLargeError, match="HTTP 413"):
        await create_chat_completion(
            AIProviderConfig(
                base_url="https://provider.example/v1",
                model="test-model",
                api_key=None,
                timeout_seconds=10,
                allowlist=(),
                source="global",
            ),
            [{"role": "user", "content": "hello"}],
        )


@pytest.mark.asyncio
async def test_provider_413_retries_once_with_compact_tools_and_history(monkeypatch):
    captured: list[dict] = []
    original_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        if len(captured) == 1:
            return httpx.Response(413, json={"error": {"message": "context limit"}})
        return _sse_response(
            {"choices": [{"delta": {"content": "OK"}, "finish_reason": None}]},
        )

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example/v1"),
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "probe",
                "description": "A verbose tool description " * 100,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "value": {
                            "type": "string",
                            "description": "A verbose parameter description " * 100,
                        }
                    },
                },
            },
        }
    ]

    message = await create_chat_completion(
        AIProviderConfig(
            base_url="https://provider.example/v1",
            model="test-model",
            api_key=None,
            timeout_seconds=10,
            allowlist=(),
            source="global",
            max_completion_tokens=2048,
        ),
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "request"},
        ],
        tools=tools,
        stream=True,
    )

    assert message["content"] == "OK"
    assert len(captured) == 2
    assert captured[0]["max_completion_tokens"] == 2048
    assert captured[1]["max_completion_tokens"] == 512
    assert "description" not in captured[1]["tools"][0]["function"]
