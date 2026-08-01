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


def test_browser_stream_uses_sanitized_markdown_and_authenticated_sse():
    script = (PROJECT_ROOT / "static" / "js" / "ai-assistant.js").read_text(encoding="utf-8")
    base = (PROJECT_ROOT / "templates" / "base.html").read_text(encoding="utf-8")

    assert "DOMPurify.sanitize" in script
    assert "FORBID_TAGS" in script
    assert "/events/stream?after=" in script
    assert "new WebSocket" not in script
    assert "marked.umd.js" in base
    assert "purify.min.js" in base


def test_pending_write_tools_open_a_yes_no_approval_prompt():
    script = (PROJECT_ROOT / "static" / "js" / "ai-assistant.js").read_text(encoding="utf-8")
    chinese = (PROJECT_ROOT / "static" / "locales" / "zh-CN.json").read_text(encoding="utf-8")

    assert "window.showConfirm(" in script
    assert "showApprovalPrompt(tool, card);" in script
    assert "'approve', card" in script
    assert "'reject', card" in script
    assert '"approvalPromptTitle": "确认服务器变更"' in chinese


def test_write_tool_queue_states_are_rendered_live():
    script = (PROJECT_ROOT / "static" / "js" / "ai-assistant.js").read_text(encoding="utf-8")
    chinese = (PROJECT_ROOT / "static" / "locales" / "zh-CN.json").read_text(encoding="utf-8")

    assert "event.type === 'tool_queued'" in script
    assert "upsertToolStatus(payload, 'queued')" in script
    assert "result.status === 'queued'" in script
    assert '"queued": "已排队，等待执行"' in chinese


def test_background_task_viewer_refreshes_from_the_task_api():
    script = (PROJECT_ROOT / "static" / "js" / "ai-assistant.js").read_text(encoding="utf-8")
    base = (PROJECT_ROOT / "templates" / "base.html").read_text(encoding="utf-8")

    assert "/api/ai/tasks" in script
    assert "scheduleBackgroundTaskRefresh" in script
    assert "ai-background-task-list" in base
    assert "['queued', 'running', 'pending_approval']" in script


def test_status_bar_shows_spinner_and_dots_while_active():
    script = (PROJECT_ROOT / "static" / "js" / "ai-assistant.js").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")

    assert "ai-status-active" in script
    assert "ai-status-dots" in script
    assert "ai-card-pulse" in css
    assert "ai-spinner" in css
    assert "ai-dots" in css


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
    script = (PROJECT_ROOT / "static" / "js" / "ai-assistant.js").read_text(encoding="utf-8")
    assert "_DIAGNOSTIC_PHASE_MESSAGES" in diag_code
    assert "_emit_readable_progress" in diag_code
    assert "Isolating plugin groups" in diag_code
    assert "diagnostic_progress" in script
