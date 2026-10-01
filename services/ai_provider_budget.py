"""OpenAI-compatible provider facade with shared transport and bounded parsing."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from services.ai.errors import AIPayloadTooLargeError
from services.ai.streaming import (
    consume_chat_completion_stream,
    consume_responses_stream,
    iter_sse_data,
    normalize_responses_message,
)

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

ADAPTIVE_PROVIDER_REQUEST_BYTES = 16 * 1024

MAX_PROVIDER_MESSAGE_CONTENT_BYTES = 32 * 1024

ADAPTIVE_MAX_COMPLETION_TOKENS = 512

ADAPTIVE_TOOL_DESCRIPTION_BYTES = 0

_SCHEMA_METADATA_KEYS = frozenset({"title", "default", "examples", "$schema"})

logger = logging.getLogger(__name__)

_consume_chat_completion_stream = consume_chat_completion_stream

_consume_responses_stream = consume_responses_stream

_iter_sse_data = iter_sse_data

_normalize_responses_message = normalize_responses_message


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
