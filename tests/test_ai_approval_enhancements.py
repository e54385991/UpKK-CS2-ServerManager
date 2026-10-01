"""Security and planning coverage for the enhanced CS2 agent tools."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from api.routes import ai as ai_routes
from modules.models import AIMessage, AIToolRun
from modules.schemas.ai import AIToolDecisionRequest
from modules.utils import get_current_time
from services import ai_orchestrator
from services.ai_prompt import CORE_RULES
from services.ai_tools import (
    FilePatchInput,
    ServerOperationInput,
)
from services.plugin_installation import (
    _build_backup_command,
    _build_rollback_command,
)
from tests.ai_agent_enhancements_fakes import (
    _startup_server as _startup_server,
)
from tests.ai_agent_enhancements_fakes import (
    _StartupDB as _StartupDB,
)
from tests.ai_agent_enhancements_fakes import (
    _StartupLock as _StartupLock,
)


def test_write_approval_and_rollback_are_revision_bound():
    tool_run = AIToolRun(
        run_id="run",
        tool_call_id="call",
        tool_name="apply_github_plugin_install",
        arguments={"repo_url": "https://github.com/owner/repo"},
        arguments_hash="a" * 64,
        risk="write",
        requires_approval=True,
    )
    assert tool_run.approval_expires_at is None
    backup = _build_backup_command("/tmp/source", "/srv/csgo", "/srv/.upkk/backup")
    rollback = _build_rollback_command("/srv/csgo", "/srv/.upkk/backup")
    assert "manifest.tsv" in backup
    assert "--no-dereference" in backup
    assert "manifest.tsv" in rollback
    assert "--remove-destination" in rollback


def test_requested_changes_create_a_panel_approval_instead_of_only_text():
    assert "call the apply tool in the same run" in CORE_RULES
    assert "Never replace that tool call with text" in CORE_RULES
    assert "at most one write tool" in CORE_RULES
    assert "panel-managed frameworks" in CORE_RULES
    assert "When a tool result says restart_required" in CORE_RULES
    assert "Never use values such as new" in CORE_RULES


def test_framework_operations_and_file_revisions_have_strict_tool_contracts():
    assert ServerOperationInput(operation="install_metamod").operation == "install_metamod"
    assert (
        ServerOperationInput(operation="install_counterstrikesharp").operation
        == "install_counterstrikesharp"
    )
    with pytest.raises(ValidationError, match="never use 'new'"):
        FilePatchInput(
            relative_path="cs2/game/csgo/addons/example/config.json",
            expected_revision="new",
            content="{}",
        )


def test_multiple_write_tools_are_rejected_before_approval_rows_are_created():
    with pytest.raises(ai_orchestrator.AIProviderError, match="multiple write tools"):
        ai_orchestrator._validate_write_tool_batch(["apply_workshop_map", "apply_plugin_plan"])

    ai_orchestrator._validate_write_tool_batch(["list_installed_plugins", "apply_workshop_map"])


@pytest.mark.asyncio
async def test_legacy_multi_write_approval_batch_is_cancelled(monkeypatch):
    now = get_current_time()
    run = SimpleNamespace(
        id="run-legacy",
        conversation_id="conversation-legacy",
        status="waiting_approval",
        error=None,
        completed_at=None,
    )
    tools = [
        SimpleNamespace(
            id="tool-plugin",
            run_id=run.id,
            tool_call_id="call-plugin",
            tool_name="apply_plugin_plan",
            risk="write",
            status="queued",
            approval_expires_at=now + timedelta(minutes=5),
            error=None,
            result=None,
            completed_at=None,
        ),
        SimpleNamespace(
            id="tool-workshop",
            run_id=run.id,
            tool_call_id="call-workshop",
            tool_name="apply_workshop_map",
            risk="write",
            status="pending_approval",
            approval_expires_at=now + timedelta(minutes=5),
            error=None,
            result=None,
            completed_at=None,
        ),
    ]

    class Result:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return self

        def all(self):
            return self.values

    class DB:
        def __init__(self):
            self.results = [Result([run]), Result(tools)]
            self.added = []
            self.commits = 0

        async def execute(self, _statement):
            return self.results.pop(0)

        def add(self, item):
            self.added.append(item)

        async def commit(self):
            self.commits += 1

    db = DB()
    terminal = await ai_orchestrator.reconcile_waiting_approval_runs(db, user_id=8)

    assert terminal == {run.id}
    assert run.status == "cancelled"
    assert {item.status for item in tools} == {"cancelled"}
    messages = [item for item in db.added if isinstance(item, AIMessage)]
    assert len(messages) == 3
    assert messages[-1].tool_name == ai_orchestrator.RUN_ERROR_TOOL_NAME
    assert messages[-1].visible is True
    assert db.commits == 1


@pytest.mark.asyncio
async def test_expired_approval_closes_run_and_tool():
    run = SimpleNamespace(
        id="run-expired",
        conversation_id="conversation-expired",
        status="waiting_approval",
        error=None,
        completed_at=None,
    )
    tool = SimpleNamespace(
        id="tool-expired",
        run_id=run.id,
        tool_call_id="call-expired",
        tool_name="apply_plugin_plan",
        risk="write",
        status="pending_approval",
        approval_expires_at=get_current_time() - timedelta(seconds=1),
        progress_snapshot={
            "steps": [
                {
                    "id": "plugin:17",
                    "label": "Install Plugin",
                    "status": "pending",
                }
            ]
        },
        progress_updated_at=None,
        error=None,
        result=None,
        completed_at=None,
    )

    class Result:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return self

        def all(self):
            return self.values

    class DB:
        def __init__(self):
            self.results = [Result([run]), Result([tool])]

        async def execute(self, _statement):
            return self.results.pop(0)

        def add(self, _item):
            pass

        async def commit(self):
            pass

    terminal = await ai_orchestrator.reconcile_waiting_approval_runs(DB(), run_id=run.id)

    assert terminal == {run.id}
    assert run.status == "expired"
    assert tool.status == "expired"
    assert tool.completed_at is not None
    assert tool.progress_snapshot["steps"][0]["status"] == "interrupted"
    assert tool.progress_snapshot["current_step"] is None


@pytest.mark.asyncio
async def test_write_approval_is_queued_instead_of_rejected_while_another_runs(monkeypatch):
    run = SimpleNamespace(id="run-1", server_id=None)
    item = SimpleNamespace(
        id="tool-1",
        run_id=run.id,
        tool_name="apply_plugin_plan",
        arguments={},
        arguments_hash="a" * 64,
        risk="write",
        status="pending_approval",
        requires_approval=True,
        approval_expires_at=get_current_time() + timedelta(minutes=5),
        approved_by=None,
        approved_at=None,
    )

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def scalar_one(self):
            return self.value

    class DB:
        def __init__(self):
            self.results = [Result(item), Result(0)]

        async def execute(self, _statement):
            return self.results.pop(0)

        def add(self, _item):
            pass

        async def commit(self):
            pass

    async def run_for_user(_db, _user, _run_id):
        return run

    def schedule(coroutine):
        coroutine.close()

    monkeypatch.setattr(ai_routes, "_run_for_user", run_for_user)
    monkeypatch.setattr(
        ai_routes,
        "reconcile_waiting_approval_runs",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(ai_routes.ai_task_registry, "create", schedule)

    result = await ai_routes.decide_ai_tool(
        run.id,
        item.id,
        AIToolDecisionRequest(decision="approve", arguments_hash=item.arguments_hash),
        DB(),
        SimpleNamespace(id=8),
    )

    assert result == {"status": "queued"}
    assert item.status == "queued"


@pytest.mark.asyncio
async def test_queued_write_emits_queue_then_execution_status(monkeypatch):
    _serialized, arguments_hash = ai_orchestrator.canonical_arguments({})
    user = SimpleNamespace(id=8, is_active=True)
    run = SimpleNamespace(id="run-2", conversation_id="conversation-2")
    tool = SimpleNamespace(
        id="tool-2",
        tool_name="apply_plugin_plan",
        arguments={},
        arguments_hash=arguments_hash,
        risk="write",
        requires_approval=True,
        approved_by=user.id,
        approved_at=get_current_time() - timedelta(minutes=20),
        approval_expires_at=get_current_time() - timedelta(minutes=5),
        status="queued",
        result=None,
        error=None,
        completed_at=None,
        tool_call_id="call-2",
    )
    events = []
    lock_calls = []

    class DB:
        async def get(self, _model, _id):
            return user

        def add(self, _item):
            pass

        async def commit(self):
            pass

    class Lock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _traceback):
            return None

    class LockService:
        def get(self, *args, **kwargs):
            lock_calls.append((args, kwargs))
            return Lock()

    async def emit(_run_id, event_type, _payload):
        events.append(event_type)

    monkeypatch.setattr(ai_orchestrator, "maintenance_lock_service", LockService())
    monkeypatch.setattr(ai_orchestrator, "_emit", emit)
    monkeypatch.setattr(ai_orchestrator, "execute_tool", AsyncMock(return_value={"success": True}))

    await ai_orchestrator._execute_tool_run(DB(), run, tool, user, None)

    assert lock_calls == [
        (
            (-(user.id + 1),),
            {
                "operation": "ai_user_write",
                "wait": True,
                "wait_timeout": ai_orchestrator.AI_WRITE_QUEUE_WAIT_SECONDS,
                "ttl": ai_orchestrator.AI_WRITE_LOCK_TTL,
            },
        )
    ]
    assert events == ["tool_queued", "tool_started", "tool_completed"]
    assert tool.status == "completed"


@pytest.mark.asyncio
async def test_repeated_read_uses_precomputed_result_without_reexecuting_tool(monkeypatch):
    user = SimpleNamespace(id=8, is_active=True)
    run = SimpleNamespace(id="run-repeated-read", conversation_id="conversation-repeated-read")
    tool = SimpleNamespace(
        id="tool-repeated-read",
        tool_name="search_server_files",
        arguments={"query": "CS2-SimpleAdmin"},
        arguments_hash="a" * 64,
        risk="read",
        requires_approval=False,
        status="pending",
        result=None,
        error=None,
        completed_at=None,
        tool_call_id="call-repeated-read",
        progress_snapshot={"steps": []},
        progress_updated_at=None,
    )
    reused = {
        "success": True,
        "duplicate_call": True,
        "previous_result": {"success": True, "matches": []},
    }
    events = []

    class DB:
        async def get(self, _model, _id):
            return user

        def add(self, _item):
            pass

        async def commit(self):
            pass

    async def emit(_run_id, event_type, _payload):
        events.append(event_type)

    execute = AsyncMock()
    monkeypatch.setattr(ai_orchestrator, "_emit", emit)
    monkeypatch.setattr(ai_orchestrator, "execute_tool", execute)

    await ai_orchestrator._execute_tool_run(DB(), run, tool, user, None, precomputed_result=reused)

    execute.assert_not_awaited()
    assert events == ["tool_started", "tool_completed"]
    assert tool.status == "completed"
    assert tool.result == reused


def test_approval_plan_snapshots_have_stable_workshop_and_plugin_step_ids():
    workshop_plan, workshop_progress = ai_orchestrator._build_plan_snapshots(
        "apply_workshop_map",
        {
            "target": {"name": "kz_variety_x"},
            "steps": [
                {"action": "install_framework", "framework": "metamod"},
                {"action": "install_framework", "framework": "counterstrikesharp"},
                {"action": "install_market_plugin", "title": "MapChooser"},
                {"action": "restart_server"},
                {"action": "patch_plugin_config"},
                {"action": "append_map", "name": "kz_variety_x"},
                {"action": "verify"},
            ],
        },
    )

    assert [step["id"] for step in workshop_plan["steps"]] == [
        "install_metamod",
        "install_counterstrikesharp",
        "install_mapchooser",
        "restart_server",
        "patch_plugin_config",
        "append_map",
        "verify",
    ]
    assert workshop_progress["total"] == 7
    assert workshop_progress["message"] == "Waiting for approval"

    plugin_plan, plugin_progress = ai_orchestrator._build_plan_snapshots(
        "apply_plugin_plan",
        {
            "steps": [
                {"plugin_id": 17, "title": "Dependency", "status": "already_installed"},
                {"plugin_id": 24, "title": "Target", "status": "install"},
            ]
        },
    )
    assert [step["id"] for step in plugin_plan["steps"]] == ["plugin:17", "plugin:24"]
    assert plugin_progress["steps"][0]["status"] == "skipped"
    assert plugin_progress["completed"] == 1


@pytest.mark.asyncio
async def test_structured_tool_progress_is_persisted_before_completion(monkeypatch):
    user = SimpleNamespace(id=8, is_active=True)
    run = SimpleNamespace(id="run-progress", conversation_id="conversation-progress")
    _plan, progress = ai_orchestrator._build_plan_snapshots(
        "apply_plugin_plan",
        {"steps": [{"plugin_id": 17, "title": "Plugin", "status": "install"}]},
    )
    tool = SimpleNamespace(
        id="tool-progress",
        tool_name="apply_plugin_plan",
        arguments={},
        arguments_hash="a" * 64,
        risk="read",
        requires_approval=False,
        status="pending",
        result=None,
        error=None,
        completed_at=None,
        tool_call_id="call-progress",
        progress_snapshot=progress,
        progress_updated_at=None,
    )
    events = []

    class DB:
        def __init__(self):
            self.commits = 0

        async def get(self, _model, _id):
            return user

        def add(self, _item):
            pass

        async def commit(self):
            self.commits += 1

    async def execute(_name, _arguments, context):
        await context.emit(
            "tool_progress",
            {
                "message": "Installing Plugin",
                "step_id": "plugin:17",
                "step_status": "running",
            },
        )
        return {"success": True}

    async def emit(_run_id, event_type, payload):
        events.append((event_type, payload))

    db = DB()
    monkeypatch.setattr(ai_orchestrator, "execute_tool", execute)
    monkeypatch.setattr(ai_orchestrator, "_emit", emit)

    await ai_orchestrator._execute_tool_run(db, run, tool, user, None)

    assert db.commits >= 3
    assert [step["status"] for step in tool.progress_snapshot["steps"]] == ["completed"]
    assert tool.progress_snapshot["message"] == "Operation completed"
    assert any(event_type == "tool_progress" for event_type, _payload in events)


@pytest.mark.asyncio
async def test_unsuccessful_tool_result_is_not_marked_completed(monkeypatch):
    user = SimpleNamespace(id=8, is_active=True)
    run = SimpleNamespace(id="run-failed-result", conversation_id="conversation-failed-result")
    tool = SimpleNamespace(
        id="tool-failed-result",
        tool_name="apply_workshop_map",
        arguments={},
        arguments_hash="a" * 64,
        risk="read",
        requires_approval=False,
        status="pending",
        result=None,
        error=None,
        completed_at=None,
        tool_call_id="call-failed-result",
        progress_snapshot={"steps": []},
        progress_updated_at=None,
    )

    class DB:
        async def get(self, _model, _id):
            return user

        def add(self, _item):
            pass

        async def commit(self):
            pass

    events = []

    async def emit(_run_id, event_type, _payload):
        events.append(event_type)

    monkeypatch.setattr(ai_orchestrator, "_emit", emit)
    monkeypatch.setattr(
        ai_orchestrator,
        "execute_tool",
        AsyncMock(return_value={"success": False, "message": "Remote verification failed"}),
    )

    await ai_orchestrator._execute_tool_run(DB(), run, tool, user, None)

    assert tool.status == "failed"
    assert tool.error == "Remote verification failed"
    assert events[-1] == "tool_failed"
