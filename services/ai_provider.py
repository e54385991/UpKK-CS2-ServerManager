"""OpenAI-compatible provider facade with shared transport and bounded parsing."""

from __future__ import annotations

import json
import secrets
from dataclasses import replace
from functools import partial
from typing import Any
from urllib.parse import urlsplit

import httpx

from services.ai.errors import AIPayloadTooLargeError, AIProviderError, transient_provider_error
from services.ai.progress import ProgressCallback, StreamProgress
from services.ai.transport import ai_provider_transport
from services.ai_security import (
    MAX_PROVIDER_RESPONSE_BYTES,
    AIConfigurationError,
    AIProviderConfig,
    redact_sensitive_text,
    validate_provider_endpoint,
)
from services.http_retry import BackgroundRetry, retry_after_seconds

from .ai_provider_budget import _SCHEMA_METADATA_KEYS as _SCHEMA_METADATA_KEYS
from .ai_provider_budget import ADAPTIVE_MAX_COMPLETION_TOKENS as ADAPTIVE_MAX_COMPLETION_TOKENS
from .ai_provider_budget import ADAPTIVE_PROVIDER_REQUEST_BYTES as ADAPTIVE_PROVIDER_REQUEST_BYTES
from .ai_provider_budget import ADAPTIVE_TOOL_DESCRIPTION_BYTES as ADAPTIVE_TOOL_DESCRIPTION_BYTES
from .ai_provider_budget import CONTEXT_WINDOW_TOKEN_PRESETS as CONTEXT_WINDOW_TOKEN_PRESETS
from .ai_provider_budget import DEFAULT_CONTEXT_WINDOW_TOKENS as DEFAULT_CONTEXT_WINDOW_TOKENS
from .ai_provider_budget import (
    MAX_PROVIDER_MESSAGE_CONTENT_BYTES as MAX_PROVIDER_MESSAGE_CONTENT_BYTES,
)
from .ai_provider_budget import MAX_PROVIDER_REQUEST_BYTES as MAX_PROVIDER_REQUEST_BYTES
from .ai_provider_budget import TextDeltaCallback as TextDeltaCallback
from .ai_provider_budget import _compact_message as _compact_message
from .ai_provider_budget import _compact_messages as _compact_messages
from .ai_provider_budget import _compact_messages_to_budget as _compact_messages_to_budget
from .ai_provider_budget import _compact_schema as _compact_schema
from .ai_provider_budget import _compact_tools as _compact_tools
from .ai_provider_budget import _consume_chat_completion_stream as _consume_chat_completion_stream
from .ai_provider_budget import _consume_responses_stream as _consume_responses_stream
from .ai_provider_budget import _estimated_tokens as _estimated_tokens
from .ai_provider_budget import _iter_sse_data as _iter_sse_data
from .ai_provider_budget import _json_size as _json_size
from .ai_provider_budget import _message_groups as _message_groups
from .ai_provider_budget import _normalize_responses_message as _normalize_responses_message
from .ai_provider_budget import (
    _normalized_context_window_tokens as _normalized_context_window_tokens,
)
from .ai_provider_budget import _truncate_text as _truncate_text
from .ai_provider_budget import logger as logger
from .ai_provider_protocol import _chat_completions_payload as _chat_completions_payload
from .ai_provider_protocol import _responses_input as _responses_input
from .ai_provider_protocol import _responses_payload as _responses_payload
from .ai_provider_protocol import _responses_tool as _responses_tool
from .ai_provider_protocol import _responses_tool_choice as _responses_tool_choice


def _validate_message_payload(message: dict[str, Any]) -> dict[str, Any]:
    if not message.get("tool_calls") and not str(message.get("content") or "").strip():
        raise AIProviderError("AI provider returned neither text nor tool calls")
    return message


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
    progress: StreamProgress | None = None,
) -> tuple[dict[str, Any] | None, bytes | None]:
    if response.status_code < 200 or response.status_code >= 300:
        content = await _read_limited_response(response)
        error = _status_error(response, content)
        if error is not None:
            raise error
    if not stream or "text/event-stream" not in response.headers.get("content-type", "").lower():
        # Gateways may return JSON errors (or a buffered completion) despite stream=True.
        return None, await _read_limited_response(response)
    if config.api_protocol == "responses":
        return await _consume_responses_stream(response, on_text_delta, progress), None
    return await _consume_chat_completion_stream(response, on_text_delta, progress), None


def _decode_provider_message(config: AIProviderConfig, content: bytes) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIProviderError("AI provider returned invalid JSON", retryable=True) from exc
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
    if (
        isinstance(error, AIProviderError)
        and error.retryable
        and not isinstance(error, AIPayloadTooLargeError)
    ):
        return error.retry_after
    return None


async def _request_message(
    config: AIProviderConfig,
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    stream: bool,
    on_text_delta: TextDeltaCallback | None,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    progress = StreamProgress(on_progress, payload) if on_progress is not None else None
    if progress is not None:
        await progress.emit(force=True)
    await ai_provider_transport.acquire_rpm(
        config.requests_per_minute, config.base_url, config.api_key
    )
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
            progress=progress,
        )
    if message is None:
        assert content is not None
        message = _decode_provider_message(config, content)
    message = _validate_message_payload(message)
    if progress is not None:
        await progress.finish(message)
    return message


def _append_request_variant(
    request_variants: list[
        tuple[str, AIProviderConfig, list[dict[str, Any]], list[dict[str, Any]] | None]
    ],
    messages: list[dict[str, Any]],
    tool_choice: str | dict[str, Any] | None,
    stream: bool,
    base_url: str,
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


async def create_chat_completion(
    config: AIProviderConfig,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    stream: bool = False,
    on_text_delta: TextDeltaCallback | None = None,
    retry: BackgroundRetry | None = None,
    on_progress: ProgressCallback | None = None,
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

    add_variant = partial(
        _append_request_variant, request_variants, messages, tool_choice, stream, base_url
    )

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
                    on_progress,
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
