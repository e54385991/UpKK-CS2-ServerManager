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
