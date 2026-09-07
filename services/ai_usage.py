"""Token accounting for one assistant run.

The provider is the source of truth whenever it reports a ``usage`` block; the
estimates here only keep the console's live counter moving for gateways that do
not. ``cached_input_tokens`` is display-only — it is already contained in the
reported prompt tokens and exists so an operator can see the upstream prompt
cache working.
"""

from __future__ import annotations

import json
from typing import Any

MAX_REPORTED_TOKENS = 10_000_000


def token_count(value: Any) -> int:
    """Return a bounded, provider-independent token estimate for fallback display."""
    if value is None:
        return 0
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        except TypeError, ValueError:
            text = str(value)
    return max(0, (len(text) + 3) // 4)


def estimate_message_tokens(messages: list[dict[str, Any]]) -> int:
    return max(1, sum(token_count(message) for message in messages))


def estimate_response_tokens(response: dict[str, Any]) -> int:
    return max(1, token_count(response.get("content")) + token_count(response.get("tool_calls")))


def cached_input_tokens(usage: dict[str, Any]) -> int:
    """Prompt tokens the upstream prompt cache served, when the provider says so.

    OpenAI-compatible providers report this under
    ``prompt_tokens_details.cached_tokens``; several gateways flatten it to a
    top-level key instead.
    """
    details = usage.get("prompt_tokens_details")
    candidates: list[Any] = []
    if isinstance(details, dict):
        candidates.append(details.get("cached_tokens"))
    candidates.extend((usage.get("cached_tokens"), usage.get("prompt_cache_hit_tokens")))
    for value in candidates:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            continue
        try:
            parsed = int(value)
        except TypeError, ValueError:
            continue
        if parsed > 0:
            return min(parsed, MAX_REPORTED_TOKENS)
    return 0


def provider_token_usage(response: dict[str, Any]) -> tuple[int, int, int] | None:
    """Return ``(input, output, cached_input)`` tokens reported by the provider."""
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None

    def first_int(*keys: str) -> int:
        for key in keys:
            value = usage.get(key)
            if isinstance(value, bool):
                continue
            if not isinstance(value, (int, float, str)):
                continue
            try:
                parsed = int(value)
            except TypeError, ValueError:
                continue
            if parsed >= 0:
                return min(parsed, MAX_REPORTED_TOKENS)
        return 0

    input_tokens = first_int("prompt_tokens", "input_tokens")
    output_tokens = first_int("completion_tokens", "output_tokens")
    if not input_tokens and not output_tokens:
        output_tokens = first_int("total_tokens")
    return input_tokens, output_tokens, min(cached_input_tokens(usage), input_tokens)
