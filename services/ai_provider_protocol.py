"""OpenAI-compatible provider facade with shared transport and bounded parsing."""

from __future__ import annotations

import json
from typing import Any

from services.ai.errors import AIProviderError
from services.ai_security import (
    AIProviderConfig,
)


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


def _response_function_calls(raw_calls: list[dict[str, Any]], items: list[dict[str, Any]]) -> None:
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
        _response_function_calls(raw_calls, items)
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
