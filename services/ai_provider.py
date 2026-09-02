"""OpenAI-compatible provider facade with shared transport and bounded parsing."""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from services.ai.errors import AIPayloadTooLargeError, AIProviderError
from services.ai.streaming import (
    consume_chat_completion_stream,
    consume_responses_stream,
    iter_sse_data,
    normalize_responses_message,
)
from services.ai.transport import ai_provider_transport
from services.ai_security import (
    MAX_PROVIDER_RESPONSE_BYTES,
    AIConfigurationError,
    AIProviderConfig,
    redact_sensitive_text,
    validate_provider_endpoint,
)

TextDeltaCallback = Callable[[str], Awaitable[None]]
DEFAULT_CONTEXT_WINDOW_TOKENS = 262_144
CONTEXT_WINDOW_TOKEN_PRESETS = (262_144, 393_216, 1_048_576)
MAX_PROVIDER_REQUEST_BYTES = 96 * 1024
MAX_PROVIDER_MESSAGE_CONTENT_BYTES = 32 * 1024
logger = logging.getLogger(__name__)

# Preserve private imports used by existing tests and extensions.
_consume_chat_completion_stream = consume_chat_completion_stream
_consume_responses_stream = consume_responses_stream
_iter_sse_data = iter_sse_data
_normalize_responses_message = normalize_responses_message


def _validate_message_payload(message: dict[str, Any]) -> dict[str, Any]:
    if not message.get("tool_calls") and not str(message.get("content") or "").strip():
        raise AIProviderError("AI provider returned neither text nor tool calls")
    return message


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode())


def _estimated_tokens(value: Any) -> int:
    """Estimate provider tokens without adding a tokenizer dependency.

    Four UTF-8 bytes per token is intentionally conservative for mixed
    Chinese/English tool payloads.  The estimate is used only for local
    compaction; providers remain the source of truth for actual usage.
    """
    return max(1, (_json_size(value) + 3) // 4)


def _normalized_context_window_tokens(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return DEFAULT_CONTEXT_WINDOW_TOKENS
    try:
        candidate = int(value)
    except TypeError, ValueError:
        return DEFAULT_CONTEXT_WINDOW_TOKENS
    return candidate if candidate in CONTEXT_WINDOW_TOKEN_PRESETS else DEFAULT_CONTEXT_WINDOW_TOKENS


def _truncate_text(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    marker = "\n[… earlier content truncated …]\n"
    marker_bytes = len(marker.encode())
    if marker_bytes >= limit:
        return encoded[:limit].decode("utf-8", errors="ignore")
    remaining = limit - marker_bytes
    head_bytes = remaining // 2
    tail_bytes = remaining - head_bytes
    head = encoded[:head_bytes].decode("utf-8", errors="ignore")
    tail = encoded[-tail_bytes:].decode("utf-8", errors="ignore")
    return f"{head}{marker}{tail}"


def _compact_message(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    if not isinstance(content, str):
        return message
    compacted = dict(message)
    compacted["content"] = _truncate_text(content, MAX_PROVIDER_MESSAGE_CONTENT_BYTES)
    return compacted


def _message_groups(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    prefix: list[dict[str, Any]] = []
    index = 0
    while index < len(messages) and messages[index].get("role") == "system":
        prefix.append(messages[index])
        index += 1

    groups: list[list[dict[str, Any]]] = []
    while index < len(messages):
        message = messages[index]
        group = [message]
        index += 1
        if message.get("role") == "assistant" and message.get("tool_calls"):
            while index < len(messages) and messages[index].get("role") == "tool":
                group.append(messages[index])
                index += 1
        groups.append(group)
    return prefix, groups


def _compact_messages(
    messages: list[dict[str, Any]],
    payload_factory: Callable[[list[dict[str, Any]]], dict[str, Any]],
    *,
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
    max_completion_tokens: int = 0,
) -> tuple[list[dict[str, Any]], bool]:
    context_limit = _normalized_context_window_tokens(context_window_tokens)
    output_reserve = max(int(max_completion_tokens or 0), 0)
    input_token_limit = max(context_limit - output_reserve, 1)
    original_payload = payload_factory(messages)
    original_size = _json_size(original_payload)
    if (
        original_size <= MAX_PROVIDER_REQUEST_BYTES
        and _estimated_tokens(original_payload) <= input_token_limit
    ):
        return messages, False

    prefix, groups = _message_groups(messages)
    kept = list(groups)
    while len(kept) > 1:
        candidate = prefix + [message for group in kept for message in group]
        candidate_payload = payload_factory(candidate)
        if (
            _json_size(candidate_payload) <= MAX_PROVIDER_REQUEST_BYTES
            and _estimated_tokens(candidate_payload) <= input_token_limit
        ):
            logger.warning(
                "Compacted oversized AI provider request from %d bytes to %d bytes (%d messages)",
                original_size,
                _json_size(payload_factory(candidate)),
                len(candidate),
            )
            return candidate, True
        kept.pop(0)

    candidate = prefix + [message for group in kept for message in group]
    compacted = [_compact_message(message) for message in candidate]
    compacted_payload = payload_factory(compacted)
    compacted_size = _json_size(compacted_payload)
    if (
        compacted_size > MAX_PROVIDER_REQUEST_BYTES
        or _estimated_tokens(compacted_payload) > input_token_limit
    ):
        raise AIPayloadTooLargeError(
            "AI provider request remains too large after history compaction; "
            "reduce tool output or start a new conversation"
        )
    logger.warning(
        "Truncated oversized AI provider message from %d bytes to %d bytes",
        _json_size(payload_factory(candidate)),
        compacted_size,
    )
    return compacted, True


def _chat_completions_payload(
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


def _responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert persisted Chat-style history to stateless Responses input items."""
    items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if role == "tool":
            call_id = str(message.get("tool_call_id") or "").strip()
            if not call_id:
                raise AIProviderError("Tool output is missing its tool call ID")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, default=str)
            items.append({"type": "function_call_output", "call_id": call_id, "output": content})
            continue

        if content is not None:
            items.append({"role": role, "content": content})
        raw_calls = message.get("tool_calls")
        if not raw_calls:
            continue
        if role != "assistant" or not isinstance(raw_calls, list):
            raise AIProviderError("Conversation history contains invalid tool calls")
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict):
                raise AIProviderError("Conversation history contains an invalid tool call")
            function = raw_call.get("function")
            if not isinstance(function, dict):
                raise AIProviderError("Conversation history contains an invalid function call")
            call_id = str(raw_call.get("id") or "").strip()
            name = str(function.get("name") or "").strip()
            if not call_id or not name:
                raise AIProviderError("Conversation history contains an incomplete function call")
            arguments = function.get("arguments", "{}")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
            items.append(
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": name,
                    "arguments": arguments,
                }
            )
    return items


def _responses_tool(tool: dict[str, Any]) -> dict[str, Any]:
    if tool.get("type") != "function" or not isinstance(tool.get("function"), dict):
        return tool
    function = tool["function"]
    converted: dict[str, Any] = {
        "type": "function",
        "name": function.get("name"),
        "parameters": function.get("parameters", {"type": "object", "properties": {}}),
        "strict": bool(function.get("strict", False)),
    }
    if function.get("description") is not None:
        converted["description"] = function["description"]
    return converted


def _responses_tool_choice(tool_choice: str | dict[str, Any] | None) -> str | dict[str, Any]:
    if tool_choice is None:
        return "auto"
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        function = tool_choice.get("function")
        if isinstance(function, dict) and function.get("name"):
            return {"type": "function", "name": function["name"]}
    return tool_choice


def _responses_payload(
    config: AIProviderConfig,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
    stream: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": config.model,
        "input": _responses_input(messages),
        "stream": stream,
        "store": False,
    }
    if config.token_limit_parameter != "omit":
        payload["max_output_tokens"] = config.max_completion_tokens
    optional_parameters = {
        "temperature": config.temperature,
        "top_p": config.top_p,
        "frequency_penalty": config.frequency_penalty,
        "presence_penalty": config.presence_penalty,
    }
    payload.update({key: value for key, value in optional_parameters.items() if value is not None})
    if config.reasoning_effort is not None:
        payload["reasoning"] = {"effort": config.reasoning_effort}
    if config.verbosity is not None:
        payload["text"] = {"verbosity": config.verbosity}
    if tools:
        payload["tools"] = [_responses_tool(tool) for tool in tools]
        payload["tool_choice"] = _responses_tool_choice(tool_choice)
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
        if response.status_code == 413:
            return AIPayloadTooLargeError(
                "AI provider returned HTTP 413 (request payload is too large). "
                "Conversation history was compacted; reduce tool output or start a new conversation."
            )
        return AIProviderError(f"AI provider returned HTTP {response.status_code}: {detail}")
    return None


def _provider_request(
    config: AIProviderConfig,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
    stream: bool,
    base_url: str,
) -> tuple[str, dict[str, Any]]:
    if config.api_protocol == "chat_completions":
        return (
            f"{base_url.rstrip('/')}/chat/completions",
            _chat_completions_payload(
                config,
                messages,
                tools=tools,
                tool_choice=tool_choice,
                stream=stream,
            ),
        )
    if config.api_protocol == "responses":
        return (
            f"{base_url.rstrip('/')}/responses",
            _responses_payload(
                config,
                messages,
                tools=tools,
                tool_choice=tool_choice,
                stream=stream,
            ),
        )
    raise AIConfigurationError(f"Unsupported AI API protocol: {config.api_protocol}")


async def _provider_message(
    config: AIProviderConfig,
    response: httpx.Response,
    *,
    stream: bool,
    on_text_delta: TextDeltaCallback | None,
) -> tuple[dict[str, Any] | None, bytes | None]:
    if response.status_code < 200 or response.status_code >= 300:
        content = await _read_limited_response(response)
        error = _status_error(response, content)
        if error is not None:
            raise error
    if not stream:
        return None, await _read_limited_response(response)
    if config.api_protocol == "responses":
        return await _consume_responses_stream(response, on_text_delta), None
    return await _consume_chat_completion_stream(response, on_text_delta), None


def _decode_provider_message(config: AIProviderConfig, content: bytes) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIProviderError("AI provider returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise AIProviderError("AI provider response is invalid")
    if config.api_protocol == "responses":
        return _normalize_responses_message(data)
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIProviderError("AI provider returned an invalid Chat Completions response") from exc
    if not isinstance(message, dict):
        raise AIProviderError("AI provider response message is invalid")
    if isinstance(data.get("usage"), dict):
        message = {**message, "usage": data["usage"]}
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

    def payload_factory(candidate: list[dict[str, Any]]) -> dict[str, Any]:
        return _provider_request(config, candidate, tools, tool_choice, stream, base_url)[1]

    messages, _compacted = _compact_messages(
        messages,
        payload_factory,
        context_window_tokens=getattr(
            config, "context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS
        ),
        max_completion_tokens=config.max_completion_tokens,
    )
    endpoint, payload = _provider_request(config, messages, tools, tool_choice, stream, base_url)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    try:
        async with ai_provider_transport.stream(
            "POST",
            endpoint,
            headers=headers,
            json=payload,
            timeout=httpx.Timeout(config.timeout_seconds),
        ) as response:
            message, response_content = await _provider_message(
                config,
                response,
                stream=stream,
                on_text_delta=on_text_delta,
            )
    except httpx.HTTPError as exc:
        raise AIProviderError(f"AI provider request failed: {type(exc).__name__}") from exc
    if message is None:
        assert response_content is not None
        message = _decode_provider_message(config, response_content)
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
