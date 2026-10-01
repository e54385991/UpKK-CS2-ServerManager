"""Security and planning coverage for the enhanced CS2 agent tools."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes import ai as ai_routes
from modules.models import AIMessage
from services import ai_orchestrator
from tests.ai_agent_enhancements_fakes import (
    _startup_server as _startup_server,
)
from tests.ai_agent_enhancements_fakes import (
    _StartupDB as _StartupDB,
)
from tests.ai_agent_enhancements_fakes import (
    _StartupLock as _StartupLock,
)


@pytest.mark.asyncio
async def test_failed_run_persists_a_visible_error_message(monkeypatch):
    run = SimpleNamespace(
        id="run-provider-failure",
        conversation_id="conversation-provider-failure",
        status="running",
        error=None,
        completed_at=None,
    )

    class DB:
        def __init__(self):
            self.added = []
            self.commits = 0

        def add(self, item):
            self.added.append(item)

        async def commit(self):
            self.commits += 1

    emit = AsyncMock()
    db = DB()
    monkeypatch.setattr(ai_orchestrator, "_emit", emit)

    await ai_orchestrator._fail_run(db, run, "Provider response was malformed")

    error_messages = [item for item in db.added if isinstance(item, AIMessage)]
    assert run.status == "failed"
    assert db.commits == 1
    assert len(error_messages) == 1
    assert error_messages[0].content == "Provider response was malformed"
    assert error_messages[0].tool_name == ai_orchestrator.RUN_ERROR_TOOL_NAME
    assert error_messages[0].visible is True
    emit.assert_awaited_once_with(
        run.id,
        "run_failed",
        {"error": "Provider response was malformed"},
    )


@pytest.mark.asyncio
async def test_provider_failures_retry_five_times_with_exponential_backoff(monkeypatch):
    attempts = 0

    async def completion(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts <= ai_orchestrator.AI_RETRY_MAX_ATTEMPTS:
            raise ai_orchestrator.AIProviderError(f"temporary failure {attempts}")
        return {"content": "Recovered"}

    emit = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr(ai_orchestrator, "create_chat_completion", completion)
    monkeypatch.setattr(ai_orchestrator, "_emit", emit)
    monkeypatch.setattr(ai_orchestrator.asyncio, "sleep", sleep)

    result = await ai_orchestrator._create_provider_response_with_retry(
        SimpleNamespace(),
        [{"role": "user", "content": "status"}],
        run_id="run-retry",
        round_index=3,
        server_selected=True,
    )

    assert result == {"content": "Recovered"}
    assert attempts == 6
    assert [call.args[0] for call in sleep.await_args_list] == [15, 30, 60, 120, 240]
    retry_events = [call.args for call in emit.await_args_list if call.args[1] == "run_retrying"]
    assert [args[2]["attempt"] for args in retry_events] == [1, 2, 3, 4, 5]
    assert [args[2]["delay_seconds"] for args in retry_events] == [15, 30, 60, 120, 240]


@pytest.mark.asyncio
async def test_stream_delta_emits_live_token_usage(monkeypatch):
    emit = AsyncMock()
    monkeypatch.setattr(ai_orchestrator, "_emit", emit)
    emitter = ai_orchestrator._AssistantDeltaEmitter("run-live", 2, input_tokens=123)

    await emitter.add("x" * ai_orchestrator.AI_DELTA_EVENT_CHARS)

    token_events = [call for call in emit.await_args_list if call.args[1] == "token_usage"]
    assert token_events
    payload = token_events[-1].args[2]
    assert payload["input_tokens"] == 123
    assert payload["output_tokens"] >= 24
    assert payload["streaming"] is True


@pytest.mark.asyncio
async def test_oversized_provider_request_is_not_retried(monkeypatch):
    attempts = 0

    async def completion(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise ai_orchestrator.AIPayloadTooLargeError(
            "AI provider returned HTTP 413 (request payload is too large)"
        )

    emit = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr(ai_orchestrator, "create_chat_completion", completion)
    monkeypatch.setattr(ai_orchestrator, "_emit", emit)
    monkeypatch.setattr(ai_orchestrator.asyncio, "sleep", sleep)

    with pytest.raises(ai_orchestrator.AIPayloadTooLargeError, match="413"):
        await ai_orchestrator._create_provider_response_with_retry(
            object(),
            [{"role": "user", "content": "status"}],
            run_id="run-payload-too-large",
            round_index=1,
            server_selected=True,
        )

    assert attempts == 1
    sleep.assert_not_awaited()
    assert not [call for call in emit.await_args_list if call.args[1] == "run_retrying"]


@pytest.mark.asyncio
async def test_empty_provider_response_is_retried_inside_provider_retry_loop(monkeypatch):
    attempts = 0

    async def completion(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return {"content": None, "tool_calls": None}
        return {"content": "Recovered"}

    emit = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr(ai_orchestrator, "create_chat_completion", completion)
    monkeypatch.setattr(ai_orchestrator, "_emit", emit)
    monkeypatch.setattr(ai_orchestrator.asyncio, "sleep", sleep)

    result = await ai_orchestrator._create_provider_response_with_retry(
        SimpleNamespace(),
        [{"role": "user", "content": "status"}],
        run_id="run-empty-response",
        round_index=1,
        server_selected=False,
    )

    assert result["content"] == "Recovered"
    assert attempts == 2
    assert [call.args[0] for call in sleep.await_args_list] == [15]
    retry_events = [call.args for call in emit.await_args_list if call.args[1] == "run_retrying"]
    assert retry_events[0][2]["error"] == "AI provider returned neither text nor tool calls"


@pytest.mark.asyncio
async def test_background_task_view_returns_only_non_sensitive_task_progress():
    run = SimpleNamespace(
        id="run-3",
        conversation_id="conversation-3",
        server_id=4,
        status="running",
        error=None,
        created_at=None,
        updated_at=None,
        completed_at=None,
    )
    tool = SimpleNamespace(
        id="tool-3",
        run_id=run.id,
        tool_name="apply_plugin_plan",
        risk="write",
        status="running",
        plan_snapshot={"steps": [{"id": "plugin:17", "label": "Install Plugin"}]},
        progress_snapshot={
            "steps": [{"id": "plugin:17", "label": "Install Plugin", "status": "running"}],
            "current_step": "plugin:17",
            "message": "Installing Plugin",
            "completed": 0,
            "total": 1,
        },
        progress_updated_at=None,
        error=None,
        created_at=None,
        completed_at=None,
    )
    read_tool = SimpleNamespace(
        id="tool-read",
        run_id=run.id,
        tool_name="search_plugin_market",
        risk="read",
        status="completed",
        error=None,
        created_at=None,
        completed_at=None,
    )

    class Result:
        def __init__(self, items):
            self.items = items

        def scalars(self):
            return self

        def all(self):
            return self.items

    class DB:
        def __init__(self):
            self.results = [Result([]), Result([]), Result([run]), Result([read_tool, tool])]
            self.statements = []

        async def execute(self, statement):
            self.statements.append(str(statement))
            return self.results.pop(0)

    db = DB()
    tasks = await ai_routes.list_ai_background_tasks(
        20,
        run.conversation_id,
        db,
        SimpleNamespace(id=8),
    )

    assert len(tasks) == 1
    assert tasks[0].id == run.id
    assert tasks[0].tools[0].tool_name == tool.tool_name
    assert len(tasks[0].tools) == 1
    assert tasks[0].tools[0].progress_snapshot["current_step"] == "plugin:17"
    assert not hasattr(tasks[0].tools[0], "arguments")
    assert "ai_runs.conversation_id" in db.statements[2]


@pytest.mark.asyncio
async def test_terminal_background_tasks_are_deleted_after_ten_minutes():
    run = SimpleNamespace(id="run-old", status="completed")

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [run]

    class DB:
        def __init__(self):
            self.deleted = []
            self.commits = 0

        async def execute(self, statement):
            self.statement = str(statement)
            return Result()

        async def delete(self, item):
            self.deleted.append(item)

        async def commit(self):
            self.commits += 1

    db = DB()
    deleted = await ai_orchestrator.cleanup_expired_ai_runs(db, user_id=8)

    assert ai_orchestrator.AI_BACKGROUND_TASK_RETENTION_MINUTES == 10
    assert deleted == 1
    assert db.deleted == [run]
    assert db.commits == 1
    assert "coalesce" in db.statement.lower()
    assert "ai_runs.user_id" in db.statement


@pytest.mark.asyncio
async def test_finished_background_task_can_be_deleted(monkeypatch):
    run = SimpleNamespace(id="run-finished", status="completed")

    class DB:
        def __init__(self):
            self.deleted = []
            self.commits = 0

        async def delete(self, item):
            self.deleted.append(item)

        async def commit(self):
            self.commits += 1

    db = DB()
    monkeypatch.setattr(ai_routes, "_run_for_user", AsyncMock(return_value=run))

    await ai_routes.delete_ai_background_task(run.id, db, SimpleNamespace(id=8))

    assert db.deleted == [run]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_active_background_task_cannot_be_deleted(monkeypatch):
    run = SimpleNamespace(id="run-active", status="waiting_approval")
    db = SimpleNamespace(delete=AsyncMock(), commit=AsyncMock())
    monkeypatch.setattr(ai_routes, "_run_for_user", AsyncMock(return_value=run))

    with pytest.raises(ai_routes.HTTPException) as exc_info:
        await ai_routes.delete_ai_background_task(run.id, db, SimpleNamespace(id=8))

    assert exc_info.value.status_code == 409
    db.delete.assert_not_awaited()
    db.commit.assert_not_awaited()
