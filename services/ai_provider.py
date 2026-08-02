"""Minimal OpenAI-compatible Chat Completions client."""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

from services.ai_security import (
    MAX_PROVIDER_RESPONSE_BYTES,
    AIConfigurationError,
    AIProviderConfig,
    redact_sensitive_text,
    validate_provider_endpoint,
)


class AIProviderError(RuntimeError):
    pass


def _validate_message_payload(message: dict[str, Any]) -> dict[str, Any]:
    if not message.get("tool_calls") and not str(message.get("content") or "").strip():
        raise AIProviderError("AI provider returned neither text nor tool calls")
    return message


TextDeltaCallback = Callable[[str], Awaitable[None]]
MAX_STREAMED_CONTENT_CHARS = 20_000


def _request_payload(
    config: AIProviderConfig,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
    stream: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "stream": stream,
    }
    if stream:
        payload["stream_options"] = {"include_usage": True}
    if config.token_limit_parameter != "omit":
        payload[config.token_limit_parameter] = config.max_completion_tokens
    optional_parameters = {
        "reasoning_effort": config.reasoning_effort,
        "temperature": config.temperature,
        "top_p": config.top_p,
        "frequency_penalty": config.frequency_penalty,
        "presence_penalty": config.presence_penalty,
        "verbosity": config.verbosity,
    }
    payload.update({key: value for key, value in optional_parameters.items() if value is not None})
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"
        if config.parallel_tool_calls is not None:
            payload["parallel_tool_calls"] = config.parallel_tool_calls
    return payload


async def _read_limited_response(response: httpx.Response) -> bytes:
    content = bytearray()
    async for chunk in response.aiter_bytes():
        content.extend(chunk)
        if len(content) > MAX_PROVIDER_RESPONSE_BYTES:
            raise AIProviderError("AI provider response exceeded the size limit")
    return bytes(content)


def _status_error(response: httpx.Response, content: bytes) -> AIProviderError | None:
    if 300 <= response.status_code < 400:
        return AIProviderError("AI provider redirects are not allowed")
    if response.status_code < 200 or response.status_code >= 300:
        detail = redact_sensitive_text(content.decode(errors="replace"), limit=500)
        return AIProviderError(f"AI provider returned HTTP {response.status_code}: {detail}")
    return None


async def _iter_sse_data(response: httpx.Response) -> AsyncIterator[str]:
    data_lines: list[str] = []
    received_bytes = 0
    async for line in response.aiter_lines():
        received_bytes += len(line.encode("utf-8")) + 1
        if received_bytes > MAX_PROVIDER_RESPONSE_BYTES:
            raise AIProviderError("AI provider response exceeded the size limit")
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines.clear()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            value = line[5:]
            if value.startswith(" "):
                value = value[1:]
            data_lines.append(value)
    if data_lines:
        yield "\n".join(data_lines)


async def _consume_chat_completion_stream(
    response: httpx.Response,
    on_text_delta: TextDeltaCallback | None,
) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" not in content_type:
        raise AIProviderError("AI provider did not return a standard SSE stream")

    role = "assistant"
    content_parts: list[str] = []
    content_chars = 0
    tool_fragments: dict[int, dict[str, Any]] = {}
    saw_chunk = False
    async for payload in _iter_sse_data(response):
        if payload.strip() == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AIProviderError("AI provider returned invalid JSON in its SSE stream") from exc
        if isinstance(chunk, dict) and chunk.get("error"):
            detail = redact_sensitive_text(str(chunk["error"]), limit=500)
            raise AIProviderError(f"AI provider SSE stream failed: {detail}")
        choices = chunk.get("choices") if isinstance(chunk, dict) else None
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta") if isinstance(choice, dict) else None
        if not isinstance(delta, dict):
            continue
        saw_chunk = True
        if isinstance(delta.get("role"), str):
            role = delta["role"]
        raw_content = delta.get("content")
        if raw_content is not None and not isinstance(raw_content, str):
            raise AIProviderError("AI provider returned invalid streamed text content")
        if raw_content:
            remaining = MAX_STREAMED_CONTENT_CHARS - content_chars
            text_delta = raw_content[: max(0, remaining)]
            if text_delta:
                content_parts.append(text_delta)
                content_chars += len(text_delta)
                if on_text_delta is not None:
                    await on_text_delta(text_delta)
        raw_calls = delta.get("tool_calls")
        if raw_calls is None:
            continue
        if not isinstance(raw_calls, list):
            raise AIProviderError("AI provider returned invalid streamed tool calls")
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict) or not isinstance(raw_call.get("index"), int):
                raise AIProviderError("AI provider returned an invalid streamed tool call")
            index = raw_call["index"]
            if index < 0 or index > 31:
                raise AIProviderError("AI provider returned an invalid tool call index")
            item = tool_fragments.setdefault(
                index,
                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
            )
            if raw_call.get("id") is not None:
                item["id"] = str(raw_call["id"])
            if raw_call.get("type") is not None:
                item["type"] = str(raw_call["type"])
            function = raw_call.get("function")
            if function is None:
                continue
            if not isinstance(function, dict):
                raise AIProviderError("AI provider returned an invalid streamed function call")
            if function.get("name") is not None:
                item["function"]["name"] += str(function["name"])
            if function.get("arguments") is not None:
                item["function"]["arguments"] += str(function["arguments"])

    if not saw_chunk:
        raise AIProviderError("AI provider returned an empty SSE stream")
    content = "".join(content_parts)
    message: dict[str, Any] = {"role": role, "content": content or None}
    if tool_fragments:
        message["tool_calls"] = [tool_fragments[index] for index in sorted(tool_fragments)]
    return message


async def create_chat_completion(
    config: AIProviderConfig,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    stream: bool = False,
    on_text_delta: TextDeltaCallback | None = None,
) -> dict[str, Any]:
    base_url = await validate_provider_endpoint(config.base_url, config.allowlist)
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    payload = _request_payload(
        config,
        messages,
        tools=tools,
        tool_choice=tool_choice,
        stream=stream,
    )

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout_seconds), follow_redirects=False
        ) as client:
            async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    response_content = await _read_limited_response(response)
                    error = _status_error(response, response_content)
                    if error is not None:
                        raise error
                if stream:
                    return _validate_message_payload(
                        await _consume_chat_completion_stream(response, on_text_delta)
                    )
                response_content = await _read_limited_response(response)
    except httpx.HTTPError as exc:
        raise AIProviderError(f"AI provider request failed: {type(exc).__name__}") from exc
    try:
        data = json.loads(response_content)
        message = data["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIProviderError("AI provider returned an invalid Chat Completions response") from exc
    if not isinstance(message, dict):
        raise AIProviderError("AI provider response message is invalid")
    return _validate_message_payload(message)


async def test_provider(config: AIProviderConfig) -> tuple[bool, bool, bool, str]:
    text_ok = False
    tool_ok = False
    streaming_ok = False
    try:
        message = await create_chat_completion(
            config,
            [
                {"role": "system", "content": "Reply with the single word OK."},
                {"role": "user", "content": "Connection test"},
            ],
            stream=True,
        )
        text_ok = bool(str(message.get("content") or "").strip())
        streaming_ok = text_ok
        nonce = secrets.token_hex(12)
        tool = {
            "type": "function",
            "function": {
                "name": "ai_capability_probe",
                "description": "Return the exact nonce supplied by the user.",
                "parameters": {
                    "type": "object",
                    "properties": {"nonce": {"type": "string"}},
                    "required": ["nonce"],
                    "additionalProperties": False,
                },
            },
        }
        message = await create_chat_completion(
            config,
            [{"role": "user", "content": f"Call ai_capability_probe with nonce {nonce}."}],
            tools=[tool],
            tool_choice={"type": "function", "function": {"name": "ai_capability_probe"}},
            stream=True,
        )
        calls = message.get("tool_calls")
        if isinstance(calls, list) and calls:
            function = calls[0].get("function", {})
            arguments = json.loads(function.get("arguments", "{}"))
            tool_ok = (
                function.get("name") == "ai_capability_probe" and arguments.get("nonce") == nonce
            )
    except (AIProviderError, AIConfigurationError, json.JSONDecodeError) as exc:
        return text_ok, tool_ok, streaming_ok, str(exc)
    if text_ok and tool_ok:
        return True, True, True, "Provider SSE text and streamed tool-calling tests passed"
    if not text_ok:
        return False, tool_ok, streaming_ok, "Provider did not return a usable SSE text response"
    return True, False, streaming_ok, "Provider did not return a valid streamed tool_call"
