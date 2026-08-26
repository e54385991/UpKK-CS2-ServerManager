"""Bounded parsers for Chat Completions and Responses SSE streams."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from services.ai.errors import AIProviderError
from services.ai_security import MAX_PROVIDER_RESPONSE_BYTES, redact_sensitive_text

TextDeltaCallback = Callable[[str], Awaitable[None]]
MAX_STREAMED_CONTENT_CHARS = 20_000


async def iter_sse_data(response: httpx.Response) -> AsyncIterator[str]:
    """Yield bounded SSE data records while ignoring comments and framing."""
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
            data_lines.append(value[1:] if value.startswith(" ") else value)
    if data_lines:
        yield "\n".join(data_lines)


def parse_sse_json(payload: str, *, protocol: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AIProviderError("AI provider returned invalid JSON in its SSE stream") from exc
    if not isinstance(value, dict):
        raise AIProviderError(f"AI provider returned an invalid {protocol} SSE event")
    return value


@dataclass(slots=True)
class ChatStreamAccumulator:
    role: str = "assistant"
    content_parts: list[str] = field(default_factory=list)
    content_chars: int = 0
    tool_fragments: dict[int, dict[str, Any]] = field(default_factory=dict)
    usage: dict[str, Any] | None = None
    saw_chunk: bool = False

    async def add(self, chunk: dict[str, Any], on_text_delta: TextDeltaCallback | None) -> None:
        raw_usage = chunk.get("usage")
        if isinstance(raw_usage, dict):
            self.usage = raw_usage
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return
        choice = choices[0]
        delta = choice.get("delta") if isinstance(choice, dict) else None
        if not isinstance(delta, dict):
            return
        self.saw_chunk = True
        if isinstance(delta.get("role"), str):
            self.role = delta["role"]
        await self._add_content(delta.get("content"), on_text_delta)
        self._add_tool_calls(delta.get("tool_calls"))

    async def _add_content(
        self,
        raw_content: object,
        on_text_delta: TextDeltaCallback | None,
    ) -> None:
        if raw_content is not None and not isinstance(raw_content, str):
            raise AIProviderError("AI provider returned invalid streamed text content")
        if not raw_content:
            return
        remaining = MAX_STREAMED_CONTENT_CHARS - self.content_chars
        text_delta = raw_content[: max(0, remaining)]
        if not text_delta:
            return
        self.content_parts.append(text_delta)
        self.content_chars += len(text_delta)
        if on_text_delta is not None:
            await on_text_delta(text_delta)

    def _add_tool_calls(self, raw_calls: object) -> None:
        if raw_calls is None:
            return
        if not isinstance(raw_calls, list):
            raise AIProviderError("AI provider returned invalid streamed tool calls")
        for raw_call in raw_calls:
            self._add_tool_call(raw_call)

    def _add_tool_call(self, raw_call: object) -> None:
        if not isinstance(raw_call, dict) or not isinstance(raw_call.get("index"), int):
            raise AIProviderError("AI provider returned an invalid streamed tool call")
        index = raw_call["index"]
        if index < 0 or index > 31:
            raise AIProviderError("AI provider returned an invalid tool call index")
        item = self.tool_fragments.setdefault(
            index,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if raw_call.get("id") is not None:
            item["id"] = str(raw_call["id"])
        if raw_call.get("type") is not None:
            item["type"] = str(raw_call["type"])
        function = raw_call.get("function")
        if function is None:
            return
        if not isinstance(function, dict):
            raise AIProviderError("AI provider returned an invalid streamed function call")
        if function.get("name") is not None:
            item["function"]["name"] += str(function["name"])
        if function.get("arguments") is not None:
            item["function"]["arguments"] += str(function["arguments"])

    def message(self) -> dict[str, Any]:
        if not self.saw_chunk:
            raise AIProviderError("AI provider returned an empty SSE stream")
        message: dict[str, Any] = {
            "role": self.role,
            "content": "".join(self.content_parts) or None,
        }
        if self.tool_fragments:
            message["tool_calls"] = [
                self.tool_fragments[index] for index in sorted(self.tool_fragments)
            ]
        if self.usage is not None:
            message["usage"] = self.usage
        return message


async def consume_chat_completion_stream(
    response: httpx.Response,
    on_text_delta: TextDeltaCallback | None,
) -> dict[str, Any]:
    if "text/event-stream" not in response.headers.get("content-type", "").lower():
        raise AIProviderError("AI provider did not return a standard SSE stream")
    accumulator = ChatStreamAccumulator()
    async for payload in iter_sse_data(response):
        if payload.strip() == "[DONE]":
            break
        chunk = parse_sse_json(payload, protocol="Chat Completions")
        if chunk.get("error"):
            detail = redact_sensitive_text(str(chunk["error"]), limit=500)
            raise AIProviderError(f"AI provider SSE stream failed: {detail}")
        await accumulator.add(chunk, on_text_delta)
    return accumulator.message()


def normalize_responses_message(data: dict[str, Any]) -> dict[str, Any]:
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
        _append_responses_item(item, text_parts, tool_calls)
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if isinstance(data.get("usage"), dict):
        message["usage"] = data["usage"]
    return message


def _append_responses_item(
    item: dict[str, Any],
    text_parts: list[str],
    tool_calls: list[dict[str, Any]],
) -> None:
    item_type = item.get("type")
    if item_type == "message":
        content = item.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
                elif part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                    text_parts.append(part["refusal"])
        return
    if item_type != "function_call":
        return
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


def responses_failure_detail(event: dict[str, Any]) -> str:
    if event.get("error"):
        return redact_sensitive_text(str(event["error"]), limit=500)
    response = event.get("response")
    if isinstance(response, dict):
        details = response.get("incomplete_details") or response.get("error")
        if details:
            return redact_sensitive_text(str(details), limit=500)
    return "unknown provider error"


@dataclass(slots=True)
class ResponsesStreamAccumulator:
    content_parts: list[str] = field(default_factory=list)
    content_chars: int = 0
    completed_response: dict[str, Any] | None = None
    saw_event: bool = False

    async def add(self, event: dict[str, Any], on_text_delta: TextDeltaCallback | None) -> None:
        self.saw_event = True
        event_type = event.get("type")
        if event_type == "response.output_text.delta":
            await self._add_delta(event.get("delta"), on_text_delta)
            return
        if event_type == "response.completed":
            raw_response = event.get("response")
            if not isinstance(raw_response, dict):
                raise AIProviderError("AI provider returned an invalid completed response")
            self.completed_response = raw_response
            return
        if event_type in {"error", "response.failed", "response.incomplete"} or event.get("error"):
            raise AIProviderError(
                f"AI provider Responses stream failed: {responses_failure_detail(event)}"
            )

    async def _add_delta(
        self,
        delta: object,
        on_text_delta: TextDeltaCallback | None,
    ) -> None:
        if not isinstance(delta, str):
            raise AIProviderError("AI provider returned invalid streamed text content")
        remaining = MAX_STREAMED_CONTENT_CHARS - self.content_chars
        text_delta = delta[: max(0, remaining)]
        if not text_delta:
            return
        self.content_parts.append(text_delta)
        self.content_chars += len(text_delta)
        if on_text_delta is not None:
            await on_text_delta(text_delta)

    def message(self) -> dict[str, Any]:
        if not self.saw_event:
            raise AIProviderError("AI provider returned an empty SSE stream")
        if self.completed_response is None:
            raise AIProviderError("AI provider Responses stream ended before completion")
        message = normalize_responses_message(self.completed_response)
        streamed_content = "".join(self.content_parts)
        if streamed_content:
            message["content"] = streamed_content
        return message


async def consume_responses_stream(
    response: httpx.Response,
    on_text_delta: TextDeltaCallback | None,
) -> dict[str, Any]:
    if "text/event-stream" not in response.headers.get("content-type", "").lower():
        raise AIProviderError("AI provider did not return a standard SSE stream")
    accumulator = ResponsesStreamAccumulator()
    async for payload in iter_sse_data(response):
        if payload.strip() == "[DONE]":
            break
        event = parse_sse_json(payload, protocol="Responses")
        await accumulator.add(event, on_text_delta)
    return accumulator.message()
