"""SSE fan-out and browser-stream contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.routes.ai import _encode_sse_event
from services.ai_events import AIEventHub

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _Pipeline:
    def rpush(self, *_args):
        return self

    def ltrim(self, *_args):
        return self

    def expire(self, *_args):
        return self

    async def execute(self):
        return []


def test_sse_encoding_has_exact_id_event_and_json_data():
    event = {
        "sequence": "1234567890123456789",
        "type": "assistant_delta\r\ninjected: no",
        "payload": {"delta": "# 状态\n\n运行中"},
    }

    encoded = _encode_sse_event(event)

    assert encoded.startswith("id: 1234567890123456789\n")
    assert "event: assistant_deltainjected: no\n" in encoded
    data_line = next(line for line in encoded.splitlines() if line.startswith("data: "))
    assert json.loads(data_line[6:]) == event


@pytest.mark.asyncio
async def test_ai_event_hub_fans_out_to_sse_queue(monkeypatch):
    hub = AIEventHub()
    monkeypatch.setattr(
        "services.ai_events.redis_manager.client.pipeline",
        lambda **_kwargs: _Pipeline(),
    )
    queue = await hub.subscribe_queue("run-1")

    emitted = await hub.emit("run-1", "tool_progress", {"message": "50%"})

    assert await queue.get() == emitted
    assert isinstance(emitted["sequence"], str)
    await hub.unsubscribe_queue("run-1", queue)


def test_ai_reasoning_and_tool_call_limits_are_configurable():
    orchestrator = (PROJECT_ROOT / "services" / "ai_orchestrator.py").read_text(encoding="utf-8")
    schema = (PROJECT_ROOT / "modules" / "schemas" / "ai.py").read_text(encoding="utf-8")
    form = (
        PROJECT_ROOT / "frontend" / "src" / "modules" / "settings" / "ai-settings-form.tsx"
    ).read_text(encoding="utf-8")
    wire = (PROJECT_ROOT / "frontend" / "src" / "modules" / "settings" / "ai-wire.ts").read_text(
        encoding="utf-8"
    )

    assert "DEFAULT_MAX_PROVIDER_ROUNDS = 200" in orchestrator
    assert "DEFAULT_MAX_TOOL_CALLS_PER_ROUND = 200" in orchestrator
    assert "max_tool_calls_per_round" in orchestrator
    assert "MAX_CONFIGURED_AI_LIMIT = 1000" in orchestrator
    assert "max_provider_rounds: Optional[int] = Field(default=None, ge=1, le=1000)" in schema
    assert "max_tool_calls_per_round: Optional[int] = Field(default=None, ge=1, le=1000)" in schema
    assert "maxProviderRounds" in form
    assert "maxToolCallsPerRound" in form
    assert "AI_CONTEXT_WINDOW_OPTIONS" in form
    assert "maxProviderRounds: 200" in wire
    assert "maxToolCallsPerRound: 200" in wire


def test_token_usage_is_streamed_to_the_console():
    streaming = (PROJECT_ROOT / "services" / "ai" / "streaming.py").read_text(encoding="utf-8")
    orchestrator = (PROJECT_ROOT / "services" / "ai_orchestrator.py").read_text(encoding="utf-8")
    chat = (
        PROJECT_ROOT / "frontend" / "src" / "modules" / "assistant" / "assistant-chat.tsx"
    ).read_text(encoding="utf-8")
    run = (
        PROJECT_ROOT / "frontend" / "src" / "modules" / "assistant" / "use-assistant-run.ts"
    ).read_text(encoding="utf-8")

    assert 'raw_usage = chunk.get("usage")' in streaming
    assert 'message["usage"] = self.usage' in streaming
    assert '"token_usage"' in orchestrator
    assert "_provider_token_usage(response)" in orchestrator
    assert 'event.type === "token_usage"' in run
    assert "createTextDisplayBuffer" in run
    assert "return confirm({" in chat


def test_pending_write_tools_open_a_confirmation_prompt():
    chat = (
        PROJECT_ROOT / "frontend" / "src" / "modules" / "assistant" / "assistant-chat.tsx"
    ).read_text(encoding="utf-8")
    assert "return confirm({" in chat
    assert "decideAssistantToolClient" in chat


def test_run_errors_are_preserved_on_the_orchestrator():
    orchestrator = (PROJECT_ROOT / "services" / "ai_orchestrator.py").read_text(encoding="utf-8")
    assert 'RUN_ERROR_TOOL_NAME = "__run_error__"' in orchestrator


def test_install_progress_is_forwarded_to_ai_assistant():
    install_code = (PROJECT_ROOT / "services" / "plugin_installation.py").read_text(
        encoding="utf-8"
    )
    conflict_code = (PROJECT_ROOT / "services" / "plugin_conflict_service.py").read_text(
        encoding="utf-8"
    )
    github_code = (PROJECT_ROOT / "services" / "github_plugin_plan_service.py").read_text(
        encoding="utf-8"
    )
    assert "ai_progress" in install_code
    assert "await ai_progress" in install_code
    assert "ai_progress=progress" in conflict_code
    assert "ai_progress=progress" in github_code


def test_diagnostic_progress_shows_readable_phase_messages():
    diag_code = (PROJECT_ROOT / "services" / "plugin_diagnostic_service.py").read_text(
        encoding="utf-8"
    )
    assert "_DIAGNOSTIC_PHASE_MESSAGES" in diag_code
    assert "_emit_readable_progress" in diag_code
    assert "Isolating plugin groups" in diag_code
