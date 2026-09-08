"""Numeric-only provider stream telemetry; never retain or publish reasoning text."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypedDict

from services.ai_usage import (
    provider_reasoning_tokens,
    provider_token_usage,
    stream_token_count,
    token_count,
)


class ProviderProgress(TypedDict):
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    estimated: bool
    stage: Literal["waiting", "thinking", "generating", "completed"]


ProgressCallback = Callable[[ProviderProgress], Awaitable[None]]


class StreamProgress:
    def __init__(self, callback: ProgressCallback, payload: dict[str, Any]) -> None:
        self.callback = callback
        self.value: ProviderProgress = {
            "input_tokens": token_count(payload),
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "estimated": True,
            "stage": "waiting",
        }
        self.output_chars = 0
        self.reasoning_chars = 0
        self.last_sent = 0.0
        self.last_stage = self.value["stage"]

    async def emit(self, *, force: bool = False) -> None:
        current = time.monotonic()
        if force or self.last_stage != self.value["stage"] or current - self.last_sent >= 1:
            await self.callback(self.value.copy())
            self.last_sent = current
            self.last_stage = self.value["stage"]

    def add_text(self, value: object, *, reasoning: bool = False) -> None:
        if not isinstance(value, str) or not value:
            return
        self.value["estimated"] = True
        if reasoning:
            self.reasoning_chars += len(value)
            self.value["stage"] = "thinking"
        else:
            self.output_chars += len(value)
            self.value["stage"] = "generating"
        self.value["reasoning_tokens"] = stream_token_count(self.reasoning_chars)
        self.value["output_tokens"] = stream_token_count(self.output_chars + self.reasoning_chars)

    def usage(self, response: dict[str, Any]) -> None:
        reported = provider_token_usage(response)
        raw_usage = response.get("usage")
        if (
            reported is None
            or not isinstance(raw_usage, dict)
            or not any(
                key in raw_usage
                for key in (
                    "input_tokens",
                    "prompt_tokens",
                    "output_tokens",
                    "completion_tokens",
                    "total_tokens",
                )
            )
        ):
            return
        self.value["input_tokens"], self.value["output_tokens"], _cached = reported
        reasoning = provider_reasoning_tokens(response, self.value["output_tokens"])
        self.value["reasoning_tokens"] = (
            reasoning
            if reasoning is not None
            else min(self.value["reasoning_tokens"], self.value["output_tokens"])
        )
        self.value["estimated"] = reasoning is None and self.reasoning_chars > 0

    async def chat(self, chunk: dict[str, Any]) -> None:
        choices = chunk.get("choices")
        for choice in choices if isinstance(choices, list) else []:
            delta = choice.get("delta") if isinstance(choice, dict) else None
            if isinstance(delta, dict):
                self.add_text(
                    delta.get("reasoning_content") or delta.get("reasoning"), reasoning=True
                )
                self.add_text(delta.get("content"))
        self.usage(chunk)
        await self.emit()

    async def responses(self, event: dict[str, Any]) -> None:
        event_type = event.get("type", "")
        if event_type in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"}:
            self.add_text(event.get("delta"), reasoning=True)
        elif event_type == "response.output_text.delta":
            self.add_text(event.get("delta"))
        response = event.get("response")
        if isinstance(response, dict):
            self.usage(response)
        await self.emit()

    async def finish(self, message: dict[str, Any]) -> None:
        if not self.output_chars:
            self.add_text(message.get("content"))
        self.usage(message)
        self.value["stage"] = "completed"
        await self.emit(force=True)
