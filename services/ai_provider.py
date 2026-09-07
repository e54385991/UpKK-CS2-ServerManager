"""OpenAI-compatible provider facade with shared transport and bounded parsing."""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import replace
from functools import partial
from typing import Any
from urllib.parse import urlsplit

import httpx

from services.ai.errors import AIPayloadTooLargeError, AIProviderError, transient_provider_error
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
from services.http_retry import BackgroundRetry, retry_after_seconds

TextDeltaCallback = Callable[[str], Awaitable[None]]
DEFAULT_CONTEXT_WINDOW_TOKENS = 262_144
CONTEXT_WINDOW_TOKEN_PRESETS = (
    8_192,
    16_384,
    32_768,
    65_536,
    131_072,
    262_144,
    393_216,
    1_048_576,
)
MAX_PROVIDER_REQUEST_BYTES = 48 * 1024
# Some OpenAI-compatible gateways enforce a much smaller per-request limit than
# the model advertises.  This is deliberately only used after a real 413 so
# normal providers keep the full schema and history on the first attempt.
# 16 KiB fits the common gateway ceiling while leaving enough room for the
# authenticated system prompt and the complete compact tool registry.
ADAPTIVE_PROVIDER_REQUEST_BYTES = 16 * 1024
MAX_PROVIDER_MESSAGE_CONTENT_BYTES = 32 * 1024
ADAPTIVE_MAX_COMPLETION_TOKENS = 512
ADAPTIVE_TOOL_DESCRIPTION_BYTES = 0
_SCHEMA_METADATA_KEYS = frozenset({"title", "default", "examples", "$schema"})
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

    ASCII text is approximated at four characters per token while non-ASCII
    code points count as one token.  This deliberately overestimates CJK
    content instead of relying on a byte ratio that would undercount it.
    The estimate is used only for local compaction; providers remain the
    source of truth for actual usage.
    """
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    ascii_chars = sum(char.isascii() for char in serialized)
    non_ascii_chars = len(serialized) - ascii_chars
    return max(1, (ascii_chars + 3) // 4 + non_ascii_chars)


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


def _compact_message(
    message: dict[str, Any], *, content_limit: int = MAX_PROVIDER_MESSAGE_CONTENT_BYTES
) -> dict[str, Any]:
    content = message.get("content")
    if not isinstance(content, str):
        return message
    compacted = dict(message)
    compacted["content"] = _truncate_text(content, content_limit)
    return compacted


def _compact_schema(value: Any) -> Any:
    """Keep tool validation semantics while removing verbose schema metadata."""
    if isinstance(value, list):
        return [_compact_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    compacted: dict[str, Any] = {}
    for key, item in value.items():
        if key in _SCHEMA_METADATA_KEYS:
            continue
        if key == "description" and isinstance(item, str):
            if ADAPTIVE_TOOL_DESCRIPTION_BYTES <= 0:
                continue
            compacted[key] = _truncate_text(item, ADAPTIVE_TOOL_DESCRIPTION_BYTES)
            continue
        compacted[key] = _compact_schema(item)
    return compacted


def _compact_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Return a provider-safe tool representation for a 413 recovery attempt."""
    if not tools:
        return tools
    compacted = _compact_schema(tools)
    return compacted if isinstance(compacted, list) else tools


def _compact_messages_to_budget(
    messages: list[dict[str, Any]],
    payload_factory: Callable[[list[dict[str, Any]]], dict[str, Any]],
    *,
    byte_limit: int,
) -> tuple[list[dict[str, Any]], int]:
    """Shrink message text until the complete serialized request fits."""
    content_limits = MAX_PROVIDER_MESSAGE_CONTENT_BYTES
    compacted = [_compact_message(message, content_limit=content_limits) for message in messages]
    while True:
        payload = payload_factory(compacted)
        if _json_size(payload) <= byte_limit:
            return compacted, _json_size(payload)
        if content_limits <= 256:
            return compacted, _json_size(payload)
        content_limits = max(content_limits // 2, 256)
        compacted = [
            _compact_message(message, content_limit=content_limits) for message in messages
        ]


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
    byte_limit: int = MAX_PROVIDER_REQUEST_BYTES,
) -> tuple[list[dict[str, Any]], bool]:
    context_limit = _normalized_context_window_tokens(context_window_tokens)
    output_reserve = max(int(max_completion_tokens or 0), 0)
    input_token_limit = max(context_limit - output_reserve, 1)
    original_payload = payload_factory(messages)
    original_size = _json_size(original_payload)
    if original_size <= byte_limit and _estimated_tokens(original_payload) <= input_token_limit:
        return messages, False

    prefix, groups = _message_groups(messages)
    kept = list(groups)
    while len(kept) > 1:
        candidate = prefix + [message for group in kept for message in group]
        candidate_payload = payload_factory(candidate)
        if (
            _json_size(candidate_payload) <= byte_limit
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
    compacted, compacted_size = _compact_messages_to_budget(
        candidate,
        payload_factory,
        byte_limit=byte_limit,
    )
    compacted_payload = payload_factory(compacted)
    if compacted_size > byte_limit or _estimated_tokens(compacted_payload) > input_token_limit:
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


def _provider_base_urls(base_url: str, api_protocol: str) -> tuple[str, ...]:
    """Return the configured endpoint plus the conventional ``/v1`` fallback.

    A number of OpenAI-compatible gateways publish their API below ``/v1``
    while their bare origin serves a web console.  Keep the configured URL
    authoritative, but make a root URL usable when the first response clearly
    is not an API response.  Explicit paths are never rewritten.
    """
    if api_protocol != "chat_completions":
        return (base_url,)
    parsed = urlsplit(base_url)
    if parsed.path not in {"", "/"}:
        return (base_url,)
    return (base_url, f"{base_url.rstrip('/')}/v1")


_ENDPOINT_FALLBACK_ERRORS = frozenset(
    {
        "AI provider returned invalid JSON",
        "AI provider returned an invalid Chat Completions response",
        "AI provider did not return a standard SSE stream",
        "AI provider returned an empty SSE stream",
    }
)


def _can_try_endpoint_fallback(error: AIProviderError) -> bool:
    return str(error) in _ENDPOINT_FALLBACK_ERRORS


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
            provider_detail = ""
            try:
                decoded = json.loads(content)
            except TypeError, ValueError:
                decoded = None
            if isinstance(decoded, dict):
                error = decoded.get("error")
                if isinstance(error, dict):
                    message = error.get("message")
                    if isinstance(message, str) and message.strip():
                        provider_detail = redact_sensitive_text(message, limit=240)
                elif isinstance(error, str) and error.strip():
                    provider_detail = redact_sensitive_text(error, limit=240)
            if not provider_detail and detail and detail != "{}":
                provider_detail = detail
            suffix = f" Provider detail: {provider_detail}." if provider_detail else ""
            return AIPayloadTooLargeError(
                "AI provider returned HTTP 413 (request payload is too large). "
                "Conversation history was compacted; reduce tool output or start a new conversation."
                + suffix
            )
        return AIProviderError(
            f"AI provider returned HTTP {response.status_code}: {detail}",
            retryable=response.status_code in {408, 429} or response.status_code >= 500,
            retry_after=retry_after_seconds(response.headers.get("retry-after")),
        )
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
    error = data.get("error")
    if transient_provider_error(error):
        raise AIProviderError("AI provider rate limit exceeded", retryable=True)
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


def _retry_hint(error: Exception) -> float | None:
    if isinstance(error, httpx.HTTPError):
        return 0.0
    if isinstance(error, AIProviderError) and error.retryable:
        return error.retry_after
    return None


async def _request_message(
    config: AIProviderConfig,
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    stream: bool,
    on_text_delta: TextDeltaCallback | None,
) -> dict[str, Any]:
    async with ai_provider_transport.stream(
        "POST",
        endpoint,
        headers=headers,
        json=payload,
        timeout=httpx.Timeout(config.timeout_seconds),
    ) as response:
        message, content = await _provider_message(
            config,
            response,
            stream=stream,
            on_text_delta=on_text_delta,
        )
    if message is None:
        assert content is not None
        message = _decode_provider_message(config, content)
    return _validate_message_payload(message)


async def create_chat_completion(
    config: AIProviderConfig,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    stream: bool = False,
    on_text_delta: TextDeltaCallback | None = None,
    retry: BackgroundRetry | None = None,
) -> dict[str, Any]:
    base_url = await validate_provider_endpoint(config.base_url, config.allowlist)

    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    endpoint_bases = _provider_base_urls(base_url, config.api_protocol)
    # A 413 is commonly emitted when a gateway's model-specific context/body
    # limit is smaller than the advertised model window.  Retry once with a
    # compact schema, shorter history and a bounded output reserve.  We never
    # send the original oversized request twice.
    request_variants: list[
        tuple[str, AIProviderConfig, list[dict[str, Any]], list[dict[str, Any]] | None]
    ] = []

    def add_variant(
        name: str,
        variant_config: AIProviderConfig,
        variant_tools: list[dict[str, Any]] | None,
        *,
        byte_limit: int,
    ) -> None:
        def payload_factory(candidate: list[dict[str, Any]]) -> dict[str, Any]:
            return _provider_request(
                variant_config,
                candidate,
                variant_tools,
                tool_choice,
                stream,
                base_url,
            )[1]

        variant_messages, _compacted = _compact_messages(
            messages,
            payload_factory,
            context_window_tokens=getattr(
                variant_config, "context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS
            ),
            max_completion_tokens=variant_config.max_completion_tokens,
            byte_limit=byte_limit,
        )
        request_variants.append((name, variant_config, variant_messages, variant_tools))

    add_variant("normal", config, tools, byte_limit=MAX_PROVIDER_REQUEST_BYTES)
    adaptive_config = replace(
        config,
        max_completion_tokens=min(config.max_completion_tokens, ADAPTIVE_MAX_COMPLETION_TOKENS),
        # Optional sampling/reasoning extensions are the least portable part
        # of OpenAI-compatible gateways.  Remove them on the recovery request
        # so a small-context model receives the smallest standard payload.
        reasoning_effort=None,
        temperature=None,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        verbosity=None,
        parallel_tool_calls=None,
    )
    try:
        add_variant(
            "adaptive-413",
            adaptive_config,
            _compact_tools(tools),
            byte_limit=ADAPTIVE_PROVIDER_REQUEST_BYTES,
        )
    except AIPayloadTooLargeError:
        # A very large administrator prompt can leave no room for even the
        # compact registry. Keep the normal request usable and report the
        # precise local limit if the provider also rejects it.
        logger.warning("Unable to prepare adaptive AI provider payload within the byte budget")

    for variant_index, (variant_name, variant_config, variant_messages, variant_tools) in enumerate(
        request_variants
    ):
        for index, endpoint_base in enumerate(endpoint_bases):
            endpoint, payload = _provider_request(
                variant_config,
                variant_messages,
                variant_tools,
                tool_choice,
                stream,
                endpoint_base,
            )
            request_size = _json_size(payload)
            message_size = _json_size(variant_messages)
            tool_size = _json_size(variant_tools or [])
            logger.info(
                "AI provider request variant=%s endpoint=%s bytes=%d message_bytes=%d "
                "tool_bytes=%d estimated_tokens=%d messages=%d tools=%d max_completion_tokens=%d",
                variant_name,
                endpoint,
                request_size,
                message_size,
                tool_size,
                _estimated_tokens(payload),
                len(variant_messages),
                len(variant_tools or []),
                variant_config.max_completion_tokens,
            )
            try:
                request = partial(
                    _request_message,
                    variant_config,
                    endpoint,
                    headers,
                    payload,
                    stream,
                    on_text_delta,
                )

                # Buffered streams can restart safely; never replay externally delivered deltas.
                if retry is not None and (not stream or on_text_delta is None):
                    return await retry.run(request, _retry_hint)
                return await request()
            except httpx.HTTPError as exc:
                raise AIProviderError(f"AI provider request failed: {type(exc).__name__}") from exc
            except AIProviderError as exc:
                if isinstance(exc, AIPayloadTooLargeError) and "HTTP 413" in str(exc):
                    if variant_index == 0 and len(request_variants) > 1:
                        logger.warning(
                            "AI provider rejected request variant=%s with HTTP 413; "
                            "retrying adaptive payload",
                            variant_name,
                        )
                        break
                    raise AIPayloadTooLargeError(
                        f"{exc} Outbound request was {request_size} bytes after compaction "
                        f"(messages={message_size}, tools={tool_size}, "
                        f"estimated_tokens={_estimated_tokens(payload)})."
                    ) from exc
                if index == len(endpoint_bases) - 1 or not _can_try_endpoint_fallback(exc):
                    raise
                logger.info("Configured AI origin did not expose the API; trying /v1 endpoint")

    raise AIProviderError("AI provider endpoint list is empty")


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
