"""Minimal OpenAI-compatible Chat Completions and Responses client."""

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
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": content,
                }
            )
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
        # Existing tools were designed for Chat Completions' non-strict behavior.
        "strict": bool(function.get("strict", False)),
    }
    if function.get("description") is not None:
        converted["description"] = function["description"]
    return converted


def _responses_tool_choice(
    tool_choice: str | dict[str, Any] | None,
) -> str | dict[str, Any]:
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
        # The panel persists its own bounded conversation history and audit trail.
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
    usage: dict[str, Any] | None = None
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
        raw_usage = chunk.get("usage") if isinstance(chunk, dict) else None
        if isinstance(raw_usage, dict):
            usage = raw_usage
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
    if usage is not None:
        message["usage"] = usage
    return message


def _normalize_responses_message(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("status") in {"failed", "incomplete", "cancelled"}:
        detail = data.get("error") or data.get("incomplete_details") or data.get("status")
        raise AIProviderError(
            "AI provider Responses request did not complete: "
            + redact_sensitive_text(str(detail), limit=500)
        )
    output = data.get("output")
    if not isinstance(output, list):
        raise AIProviderError("AI provider returned an invalid Responses output")
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict):
            raise AIProviderError("AI provider returned an invalid Responses output item")
        item_type = item.get("type")
        if item_type == "message":
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
                elif part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                    text_parts.append(part["refusal"])
        elif item_type == "function_call":
            call_id = str(item.get("call_id") or item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            arguments = item.get("arguments", "{}")
            if not call_id or not name or not isinstance(arguments, str):
                raise AIProviderError("AI provider returned an invalid Responses function call")
            tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            )
    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_parts) or None,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage = data.get("usage")
    if isinstance(usage, dict):
        message["usage"] = usage
    return message


def _responses_failure_detail(event: dict[str, Any]) -> str:
    error = event.get("error")
    if error:
        return redact_sensitive_text(str(error), limit=500)
    response = event.get("response")
    if isinstance(response, dict):
        details = response.get("incomplete_details") or response.get("error")
        if details:
            return redact_sensitive_text(str(details), limit=500)
    return "unknown provider error"


async def _consume_responses_stream(
    response: httpx.Response,
    on_text_delta: TextDeltaCallback | None,
) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" not in content_type:
        raise AIProviderError("AI provider did not return a standard SSE stream")

    content_parts: list[str] = []
    content_chars = 0
    completed_response: dict[str, Any] | None = None
    saw_event = False
    async for payload in _iter_sse_data(response):
        if payload.strip() == "[DONE]":
            break
        try:
            event = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AIProviderError("AI provider returned invalid JSON in its SSE stream") from exc
        if not isinstance(event, dict):
            raise AIProviderError("AI provider returned an invalid Responses SSE event")
        saw_event = True
        event_type = event.get("type")
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if not isinstance(delta, str):
                raise AIProviderError("AI provider returned invalid streamed text content")
            remaining = MAX_STREAMED_CONTENT_CHARS - content_chars
            text_delta = delta[: max(0, remaining)]
            if text_delta:
                content_parts.append(text_delta)
                content_chars += len(text_delta)
                if on_text_delta is not None:
                    await on_text_delta(text_delta)
        elif event_type == "response.completed":
            raw_response = event.get("response")
            if not isinstance(raw_response, dict):
                raise AIProviderError("AI provider returned an invalid completed response")
            completed_response = raw_response
        elif event_type in {"error", "response.failed", "response.incomplete"} or event.get(
            "error"
        ):
            detail = _responses_failure_detail(event)
            raise AIProviderError(f"AI provider Responses stream failed: {detail}")

    if not saw_event:
        raise AIProviderError("AI provider returned an empty SSE stream")
    if completed_response is None:
        raise AIProviderError("AI provider Responses stream ended before completion")
    message = _normalize_responses_message(completed_response)
    streamed_content = "".join(content_parts)
    if streamed_content:
        message["content"] = streamed_content
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
    if config.api_protocol == "chat_completions":
        endpoint = f"{base_url.rstrip('/')}/chat/completions"
        payload = _chat_completions_payload(
            config,
            messages,
            tools=tools,
            tool_choice=tool_choice,
            stream=stream,
        )
    elif config.api_protocol == "responses":
        endpoint = f"{base_url.rstrip('/')}/responses"
        payload = _responses_payload(
            config,
            messages,
            tools=tools,
            tool_choice=tool_choice,
            stream=stream,
        )
    else:
        raise AIConfigurationError(f"Unsupported AI API protocol: {config.api_protocol}")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
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
                    if config.api_protocol == "responses":
                        message = await _consume_responses_stream(response, on_text_delta)
                    else:
                        message = await _consume_chat_completion_stream(response, on_text_delta)
                    return _validate_message_payload(message)
                response_content = await _read_limited_response(response)
    except httpx.HTTPError as exc:
        raise AIProviderError(f"AI provider request failed: {type(exc).__name__}") from exc
    try:
        data = json.loads(response_content)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIProviderError("AI provider returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise AIProviderError("AI provider response is invalid")
    if config.api_protocol == "responses":
        return _validate_message_payload(_normalize_responses_message(data))
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIProviderError("AI provider returned an invalid Chat Completions response") from exc
    if not isinstance(message, dict):
        raise AIProviderError("AI provider response message is invalid")
    usage = data.get("usage")
    if isinstance(usage, dict):
        message = {**message, "usage": usage}
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
