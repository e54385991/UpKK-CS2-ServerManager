"""Minimal OpenAI-compatible Chat Completions client."""

from __future__ import annotations

import json
import secrets
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


async def create_chat_completion(
    config: AIProviderConfig,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_url = await validate_provider_endpoint(config.base_url, config.allowlist)
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "stream": False,
        "max_tokens": 2048,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout_seconds), follow_redirects=False
        ) as client:
            async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_PROVIDER_RESPONSE_BYTES:
                        raise AIProviderError("AI provider response exceeded the size limit")
                response_content = bytes(content)
    except httpx.HTTPError as exc:
        raise AIProviderError(f"AI provider request failed: {type(exc).__name__}") from exc
    if 300 <= response.status_code < 400:
        raise AIProviderError("AI provider redirects are not allowed")
    if response.status_code < 200 or response.status_code >= 300:
        detail = redact_sensitive_text(response_content.decode(errors="replace"), limit=500)
        raise AIProviderError(f"AI provider returned HTTP {response.status_code}: {detail}")
    try:
        data = json.loads(response_content)
        message = data["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIProviderError("AI provider returned an invalid Chat Completions response") from exc
    if not isinstance(message, dict):
        raise AIProviderError("AI provider response message is invalid")
    return message


async def test_provider(config: AIProviderConfig) -> tuple[bool, bool, str]:
    text_ok = False
    tool_ok = False
    try:
        message = await create_chat_completion(
            config,
            [
                {"role": "system", "content": "Reply with the single word OK."},
                {"role": "user", "content": "Connection test"},
            ],
        )
        text_ok = bool(str(message.get("content") or "").strip())
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
        )
        calls = message.get("tool_calls")
        if isinstance(calls, list) and calls:
            function = calls[0].get("function", {})
            arguments = json.loads(function.get("arguments", "{}"))
            tool_ok = (
                function.get("name") == "ai_capability_probe" and arguments.get("nonce") == nonce
            )
    except (AIProviderError, AIConfigurationError, json.JSONDecodeError) as exc:
        return text_ok, tool_ok, str(exc)
    if text_ok and tool_ok:
        return True, True, "Provider text and tool-calling tests passed"
    if not text_ok:
        return False, tool_ok, "Provider did not return a usable text response"
    return True, False, "Provider did not return a valid standard tool_call"
