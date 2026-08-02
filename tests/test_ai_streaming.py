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


def test_ai_tasks_require_target_server_confirmation_before_creation():
    script = (PROJECT_ROOT / "static" / "js" / "ai-assistant.js").read_text(encoding="utf-8")
    chinese = (PROJECT_ROOT / "static" / "locales" / "zh-CN.json").read_text(encoding="utf-8")

    assert "servers: new Map()" in script
    assert "state.servers.set(String(server.id), server)" in script
    assert "function confirmSelectedServer()" in script
    assert "await confirmSelectedServer()" in script
    assert script.index("await confirmSelectedServer()") < script.index(
        "await ensureConversation()"
    )
    assert "server.ssh_port" in script
    assert "server.game_directory" in script
    assert "serverConfirmationTitle" in script
    assert '"serverConfirmationTitle": "确认目标服务器"' in chinese


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

    assert "/api/ai/tasks?conversation_id=" in script
    assert "scheduleBackgroundTaskRefresh" in script
    assert "ai-background-task-list" in base
    assert "tool.progress_snapshot" in script
    assert "ai-task-steps" in script
    assert "openBackgroundTask(task)" in script
    assert "tool.risk === 'write'" in script
    assert ".slice(0, 2)" in script
    assert "state.conversationId !== conversationId" in script
    assert "renderBackgroundTasks([])" in script


def test_background_task_failures_keep_existing_rows_and_show_the_error():
    script = (PROJECT_ROOT / "static" / "js" / "ai-assistant.js").read_text(encoding="utf-8")
    refresh = script.split("async function refreshBackgroundTasks()", 1)[1].split(
        "function scheduleBackgroundTaskRefresh()", 1
    )[0]

    assert "failed && tool.error ? tool.error : snapshot.message" in script
    assert "task.error" in script
    assert "ai-background-task-refresh-error" in refresh
    assert "list.prepend(message)" in refresh
    assert "list.replaceChildren()" not in refresh


def test_run_errors_are_preserved_and_restored_as_error_cards():
    script = (PROJECT_ROOT / "static" / "js" / "ai-assistant.js").read_text(encoding="utf-8")
    orchestrator = (PROJECT_ROOT / "services" / "ai_orchestrator.py").read_text(encoding="utf-8")

    assert "RUN_ERROR_TOOL_NAME = '__run_error__'" in script
    assert 'RUN_ERROR_TOOL_NAME = "__run_error__"' in orchestrator
    assert "message.tool_name === RUN_ERROR_TOOL_NAME" in script
    assert "loadConversations(conversationId, false)" in script


def test_sse_and_poll_failures_use_five_exponential_retries():
    script = (PROJECT_ROOT / "static" / "js" / "ai-assistant.js").read_text(encoding="utf-8")
    chinese = (PROJECT_ROOT / "static" / "locales" / "zh-CN.json").read_text(encoding="utf-8")

    assert "const RETRY_MAX_ATTEMPTS = 5" in script
    assert "const RETRY_BASE_DELAY_MS = 15000" in script
    assert "2 ** (retryAttempt - 1)" in script
    assert "state.reconnectTimer = setTimeout(connectEvents, delay)" in script
    assert "state.pollTimer = setTimeout(pollRun, nextPollDelay)" in script
    assert "event.type === 'run_retrying'" in script
    assert "resetAssistantStream(payload)" in script
    assert '"providerRetrying"' in chinese
    assert '"sseRetrying"' in chinese
    assert '"pollRetrying"' in chinese


def test_ai_reasoning_and_tool_call_limits_are_configurable():
    orchestrator = (PROJECT_ROOT / "services" / "ai_orchestrator.py").read_text(encoding="utf-8")
    schema = (PROJECT_ROOT / "modules" / "schemas" / "ai.py").read_text(encoding="utf-8")
    template = (PROJECT_ROOT / "templates" / "system_settings.html").read_text(encoding="utf-8")
    chinese = (PROJECT_ROOT / "static" / "locales" / "zh-CN.json").read_text(encoding="utf-8")

    assert "DEFAULT_MAX_PROVIDER_ROUNDS = 200" in orchestrator
    assert "DEFAULT_MAX_TOOL_CALLS_PER_ROUND = 200" in orchestrator
    assert "max_tool_calls_per_round" in orchestrator
    assert "MAX_CONFIGURED_AI_LIMIT = 1000" in orchestrator
    assert "max_provider_rounds: Optional[int] = Field(default=None, ge=1, le=1000)" in schema
    assert "max_tool_calls_per_round: Optional[int] = Field(default=None, ge=1, le=1000)" in schema
    assert 'id="ai-admin-rounds" type="number" min="1" max="1000" value="200"' in template
    assert 'id="ai-admin-tool-calls" type="number" min="1" max="1000" value="200"' in template
    assert '"maxToolCallsPerRound": "每轮最大工具调用数"' in chinese


def test_token_usage_is_streamed_and_animated_in_the_current_session():
    provider = (PROJECT_ROOT / "services" / "ai_provider.py").read_text(encoding="utf-8")
    orchestrator = (PROJECT_ROOT / "services" / "ai_orchestrator.py").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "static" / "js" / "ai-assistant.js").read_text(encoding="utf-8")
    base = (PROJECT_ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")

    assert 'raw_usage = chunk.get("usage")' in provider
    assert 'message["usage"] = usage' in provider
    assert '"token_usage"' in orchestrator
    assert "_provider_token_usage(response)" in orchestrator
    assert "event.type === 'token_usage'" in script
    assert "requestAnimationFrame" in script
    assert "ai-input-token-count" in base
    assert "ai-output-token-count" in base
    assert "ai-token-value-pulse" in css
    assert "bi-lightning-charge-fill" in base
    assert "ai-token-activity-active" in script
    assert "setTokenActivity(true)" in script
    assert "ai-token-flash" in css


def test_finished_background_tasks_have_a_manual_delete_action():
    script = (PROJECT_ROOT / "static" / "js" / "ai-assistant.js").read_text(encoding="utf-8")
    chinese = (PROJECT_ROOT / "static" / "locales" / "zh-CN.json").read_text(encoding="utf-8")

    assert "deleteBackgroundTask(task)" in script
    assert "`/api/ai/tasks/${task.id}`" in script
    assert "!activeStatuses.includes(task.status)" in script
    assert "bi bi-trash3" in script
    assert '"deleteTask"' in chinese
    assert '"deleteTaskConfirm"' in chinese


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
