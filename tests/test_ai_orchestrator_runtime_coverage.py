"""覆盖 AI 编排器的持久化运行、重启恢复和中断路径。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.models import AIMessage, AIRun, AIToolRun
from services import ai_orchestrator as orchestrator
from services.ai_security import AIConfigurationError


class _Rows:
    def __init__(self, rows=(), scalar=None):
        self.rows = list(rows)
        self.scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)

    def scalar_one(self):
        return self.scalar

    def scalar_one_or_none(self):
        return self.scalar


class _Session:
    def __init__(self, gets=None, results=()):
        self.gets = list(gets or [])
        self.results = list(results)
        self.added = []
        self.deleted = []
        self.commits = 0

    async def get(self, *_args):
        return self.gets.pop(0) if self.gets else None

    async def execute(self, _query):
        return self.results.pop(0) if self.results else _Rows()

    async def refresh(self, _item):
        return None

    def add(self, item):
        self.added.append(item)

    async def delete(self, item):
        self.deleted.append(item)

    async def commit(self):
        self.commits += 1


class _SessionFactory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return None


def _run(**values):
    defaults = dict(
        id="run-1", conversation_id="conv-1", user_id=1, server_id=None,
        status="queued", error=None, completed_at=None,
    )
    defaults.update(values)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_process_ai_run_early_failures_and_provider_configuration(monkeypatch):
    emitted = AsyncMock()
    monkeypatch.setattr(orchestrator, "_emit", emitted)
    monkeypatch.setattr(orchestrator, "async_session_maker", _SessionFactory(_Session([None])))
    await orchestrator.process_ai_run("missing")

    run = _run()
    db = _Session([run, None, SimpleNamespace(id=1, is_active=True)])
    monkeypatch.setattr(orchestrator, "async_session_maker", _SessionFactory(db))
    await orchestrator.process_ai_run(run.id)
    assert run.status == "failed"
    assert "Conversation owner" in run.error

    run = _run(server_id=4)
    conversation = SimpleNamespace(id="conv-1", user_id=1)
    user = SimpleNamespace(id=1, is_admin=False, is_active=True)
    db = _Session([run, conversation, user])
    monkeypatch.setattr(orchestrator, "async_session_maker", _SessionFactory(db))
    monkeypatch.setattr(orchestrator.Server, "get_by_id_and_user", AsyncMock(return_value=None))
    await orchestrator.process_ai_run(run.id)
    assert "Selected server" in run.error

    run = _run()
    db = _Session([run, conversation, user])
    monkeypatch.setattr(orchestrator, "async_session_maker", _SessionFactory(db))
    monkeypatch.setattr(orchestrator, "get_effective_provider", AsyncMock(return_value=None))
    await orchestrator.process_ai_run(run.id)
    assert run.error == "No AI provider is enabled"

    run = _run()
    db = _Session([run, conversation, user])
    monkeypatch.setattr(orchestrator, "async_session_maker", _SessionFactory(db))
    monkeypatch.setattr(
        orchestrator,
        "get_effective_provider",
        AsyncMock(side_effect=AIConfigurationError("invalid provider")),
    )
    await orchestrator.process_ai_run(run.id)
    assert run.error == "invalid provider"


@pytest.mark.asyncio
async def test_process_ai_run_completes_text_response(monkeypatch):
    run = _run()
    conversation = SimpleNamespace(id="conv-1", user_id=1, updated_at=None)
    user = SimpleNamespace(id=1, is_admin=False, is_active=True)
    provider = SimpleNamespace(admin_prompt="")
    settings = SimpleNamespace(max_provider_rounds=2, max_tool_calls_per_round=2)
    db = _Session(
        [run, conversation, user],
        [
            _Rows(),  # _resume_decided_tools items
            _Rows(scalar=0),  # no pending approvals
            _Rows(scalar=1),  # latest user message
            _Rows(scalar=0),  # assistant rounds used
            _Rows(),  # existing tool runs
            _Rows(),  # provider messages
        ],
    )
    monkeypatch.setattr(orchestrator, "async_session_maker", _SessionFactory(db))
    monkeypatch.setattr(orchestrator, "get_effective_provider", AsyncMock(return_value=provider))
    monkeypatch.setattr(orchestrator.AISystemSettings, "get_or_create", AsyncMock(return_value=settings))
    monkeypatch.setattr(orchestrator, "build_system_prompt", lambda *_args: "system")
    monkeypatch.setattr(orchestrator, "_create_provider_response_with_retry", AsyncMock(return_value={"content": "hello", "usage": {"prompt_tokens": 3, "completion_tokens": 2}}))
    monkeypatch.setattr(orchestrator, "_emit", AsyncMock())
    await orchestrator.process_ai_run(run.id)
    assert run.status == "completed"
    assert any(isinstance(item, AIMessage) and item.content == "hello" for item in db.added)


@pytest.mark.asyncio
async def test_load_messages_resume_rejected_tools_and_tool_failure(monkeypatch):
    user = SimpleNamespace(id=1)
    conversation = SimpleNamespace(id="c", user_id=1)
    message = AIMessage(id=3, conversation_id="c", role="tool", content="result", visible=False, tool_name="read", tool_call_id="call")
    db = _Session(results=[_Rows(rows=[message])])
    monkeypatch.setattr(orchestrator, "build_system_prompt", lambda *_args: "system")
    messages = await orchestrator._load_provider_messages(db, conversation, user, None, "admin")
    assert messages[0] == {"role": "system", "content": "system"}
    assert messages[1]["tool_call_id"] == "call"

    run = _run(status="running")
    rejected = SimpleNamespace(
        id="tool", status="rejected", completed_at=None, result=None,
        tool_call_id="call", tool_name="tool", progress_snapshot=None,
    )
    db = _Session(results=[_Rows(rows=[rejected]), _Rows(scalar=0)])
    monkeypatch.setattr(orchestrator, "_emit", AsyncMock())
    assert await orchestrator._resume_decided_tools(db, run, user, None) is False
    assert rejected.result["error"] == "denied_by_user"

    tool = SimpleNamespace(
        id="tool", tool_call_id="call", tool_name="read", arguments={},
        arguments_hash="a" * 64, risk="read", requires_approval=False,
        status="pending", approved_by=None, approved_at=None, progress_snapshot=None,
    )
    db = _Session([SimpleNamespace(id=1, is_active=True)], [])
    monkeypatch.setattr(orchestrator, "execute_tool", AsyncMock(return_value={"success": False, "error": "remote failed"}))
    monkeypatch.setattr(orchestrator, "authorized_server", AsyncMock(return_value=None))
    monkeypatch.setattr(orchestrator, "audit_security_event", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator, "_emit", AsyncMock())
    await orchestrator._execute_tool_run(db, run, tool, user, None)
    assert tool.status == "failed"
    assert tool.error == "remote failed"


@pytest.mark.asyncio
async def test_restart_interrupts_runs_releases_locks_and_conversation_interrupt(monkeypatch):
    run = _run(status="running", user_id=1, server_id=4)
    tool = SimpleNamespace(
        id="tool", run_id="run-1", status="running", risk="write", result=None, completed_at=None,
        tool_call_id="call", tool_name="write", progress_snapshot=None,
    )
    db = _Session(results=[_Rows(rows=[run]), _Rows(rows=[tool])])
    monkeypatch.setattr(orchestrator, "async_session_maker", _SessionFactory(db))
    monkeypatch.setattr(orchestrator, "_emit", AsyncMock())
    client = SimpleNamespace(delete=AsyncMock())
    monkeypatch.setattr(orchestrator.redis_manager, "prefixed_key", lambda value: value)
    monkeypatch.setattr(orchestrator.redis_manager, "client", client)
    monkeypatch.setattr(orchestrator.maintenance_lock_service, "force_release_server_lock", AsyncMock())
    assert await orchestrator.interrupt_active_ai_runs() == 1
    assert run.status == "interrupted"
    client.delete.assert_awaited_once()

    db = _Session(results=[_Rows(scalar=None)])
    assert (await orchestrator.interrupt_conversation_run(db, SimpleNamespace(id=1, is_admin=False), "c"))["interrupted"] is False
    owner = SimpleNamespace(id=1, is_admin=False)
    active = SimpleNamespace(id="r", user_id=2, status="running", completed_at=None, error=None)
    with pytest.raises(PermissionError):
        await orchestrator.interrupt_conversation_run(
            _Session(results=[_Rows(scalar=active)]), owner, "c"
        )


def test_orchestrator_labels_snapshots_and_usage_edge_cases(monkeypatch):
    actions = [
        ("apply_workshop_map", {"action": "install_framework", "framework": "counterstrikesharp"}),
        ("apply_workshop_map", {"action": "restart_server"}),
        ("apply_workshop_map", {"action": "patch_plugin_config"}),
        ("apply_workshop_map", {"action": "append_map", "workshop_id": "123"}),
        ("apply_workshop_map", {"action": "verify"}),
        ("apply_server_startup_update", {"action": "validate_startup_revision"}),
        ("apply_server_startup_update", {"action": "save_startup_settings"}),
        ("apply_server_startup_update", {"action": "restart_server"}),
        ("apply_server_startup_update", {"action": "verify_server"}),
    ]
    for index, (name, step) in enumerate(actions):
        assert orchestrator._approval_step_id(name, step, index)
        assert orchestrator._approval_step_label(step)
    assert orchestrator._approval_step_id("other", "plain", 2) == "step:3"
    assert orchestrator._approval_step_label({"action": "install_market_plugin", "title": "MapChooser"}) == "Install MapChooser"
    assert orchestrator._approval_step_label({"action": "restart_server"}) == "Restart server"
    assert orchestrator._approval_step_label({"action": "validate_startup_revision"})
    assert orchestrator._approval_step_label({"action": "save_startup_settings"})
    assert orchestrator._approval_step_label({"action": "verify_server"})
    assert orchestrator._approval_step_label({"action": "patch_plugin_config"})
    assert orchestrator._approval_step_label({"action": "verify"})
    assert orchestrator._approval_step_label({"plugin_id": 2, "title": "Sensitive"}).startswith("Install")
    assert orchestrator._approval_step_label({"plugin_id": 2, "title": "Sensitive", "status": "already_installed"}).startswith("Skip")
    assert orchestrator._approval_step_label({"action": "unknown_action"}) == "unknown action"
    assert orchestrator._approval_step_label(None) == "Planned operation"

    monkeypatch.setattr(orchestrator, "get_current_time", lambda: datetime.now(timezone.utc))
    tool_run = SimpleNamespace(progress_snapshot=None, progress_updated_at=None)
    orchestrator._update_progress_snapshot(tool_run, {"step_id": "missing", "step_status": "bad", "message": "x"})
    assert tool_run.progress_snapshot["current_step"] is None
    tool_run.progress_snapshot = {
        "version": 1,
        "steps": [
            {"id": "a", "status": "pending", "started_at": None, "completed_at": None},
            {"id": "b", "status": "running", "started_at": "old", "completed_at": None},
            {"id": "c", "status": "failed", "started_at": "old", "completed_at": "old"},
        ],
    }
    orchestrator._finalize_progress_snapshot(tool_run, success=True, message="ok")
    assert all(step["status"] in {"completed", "failed"} for step in tool_run.progress_snapshot["steps"])
    tool_run.progress_snapshot["steps"] = [{"id": "a", "status": "pending"}, {"id": "b", "status": "running"}]
    orchestrator._finalize_progress_snapshot(tool_run, success=False, message="stop", interrupted=True)
    assert all(step["status"] == "interrupted" for step in tool_run.progress_snapshot["steps"])

    assert orchestrator._provider_token_usage({"usage": {"prompt_tokens": True, "input_tokens": "bad", "completion_tokens": -1, "total_tokens": 6}}) == (0, 6)
    assert orchestrator._provider_token_usage({"usage": {"prompt_tokens": 20_000_000, "completion_tokens": 3}}) == (10_000_000, 3)
    assert orchestrator._token_count(object()) >= 1


@pytest.mark.asyncio
async def test_orchestrator_reconcile_and_lock_edge_cases(monkeypatch):
    now = datetime.now(timezone.utc)
    expired = SimpleNamespace(
        id="expired", run_id="r1", status="pending_approval", risk="write",
        approval_expires_at=now - timedelta(seconds=1), result=None, completed_at=None,
        tool_call_id="c1", tool_name="write", progress_snapshot=None,
    )
    invalid = SimpleNamespace(
        id="invalid", run_id="r2", status="approved", risk="write",
        approval_expires_at=now + timedelta(hours=1), result=None, completed_at=None,
        tool_call_id="c2", tool_name="write", progress_snapshot=None,
    )
    invalid2 = SimpleNamespace(
        id="invalid2", run_id="r2", status="queued", risk="write",
        approval_expires_at=now + timedelta(hours=1), result=None, completed_at=None,
        tool_call_id="c3", tool_name="write", progress_snapshot=None,
    )
    run1 = SimpleNamespace(id="r1", conversation_id="c", status="waiting_approval", completed_at=None, error=None)
    run2 = SimpleNamespace(id="r2", conversation_id="c", status="waiting_approval", completed_at=None, error=None)

    class _SequenceDb(_Session):
        def __init__(self, results):
            super().__init__(results=results)

    db = _SequenceDb([_Rows(rows=[run1, run2]), _Rows(rows=[expired, invalid, invalid2])])
    changed = await orchestrator.reconcile_waiting_approval_runs(db, user_id=1, conversation_id="c", run_id="r1")
    assert changed == {"r1", "r2"} and run1.status == "expired" and run2.status == "cancelled"

    db = _SequenceDb([_Rows(rows=[run1]), _Rows(rows=[])])
    run1.status = "waiting_approval"
    run1.error = None
    assert await orchestrator.reconcile_waiting_approval_runs(db) == set()
    assert await orchestrator.cleanup_expired_ai_runs(_SequenceDb([_Rows(rows=[])]), user_id=1) == 0

    active = _SequenceDb([_Rows(scalar="active")])
    assert await orchestrator.reconcile_stale_ai_server_lock(active, 3) is False
    legacy = _SequenceDb([_Rows(scalar=None), _Rows(scalar="old")])
    clear = AsyncMock(return_value=True)
    monkeypatch.setattr(orchestrator.maintenance_lock_service, "clear_stale_server_lock", clear)
    assert await orchestrator.reconcile_stale_ai_server_lock(legacy, 3) is True
    assert "plugin_install_plan" in clear.await_args.kwargs["operation_prefixes"]


class _AsyncLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_execute_tool_run_auth_lock_precomputed_and_events(monkeypatch):
    run = _run(status="running")
    user = SimpleNamespace(id=1, is_active=True)
    server = SimpleNamespace(id=4)
    events = []

    async def emit(*args):
        events.append(args)

    async def execute(_name, _args, context):
        await context.emit("tool_progress", {"step_id": "step", "step_status": "running", "message": "working"})
        return {"success": True, "message": "done"}

    arguments = {}
    _, arguments_hash = orchestrator.canonical_arguments(arguments)
    tool = SimpleNamespace(
        id="t", tool_call_id="call", tool_name="read_server", arguments=arguments,
        arguments_hash=arguments_hash, risk="read", requires_approval=False,
        approved_by=None, approved_at=None, status="pending", result=None, error=None,
        progress_snapshot={"version": 1, "steps": [{"id": "step", "status": "pending"}]},
        completed_at=None,
    )
    db = _Session([user])
    monkeypatch.setattr(orchestrator, "_emit", emit)
    monkeypatch.setattr(orchestrator, "execute_tool", execute)
    monkeypatch.setattr(orchestrator, "authorized_server", AsyncMock(return_value=server))
    await orchestrator._execute_tool_run(db, run, tool, user, server)
    assert tool.status == "completed" and any(item[1] == "tool_completed" for item in events)

    precomputed_values = vars(tool).copy()
    precomputed_values.update(status="pending", result=None, completed_at=None)
    precomputed = SimpleNamespace(**precomputed_values)
    await orchestrator._execute_tool_run(_Session([user]), run, precomputed, user, None, {"success": True})
    assert precomputed.status == "completed"

    inactive = SimpleNamespace(id=1, is_active=False)
    db = _Session([inactive])
    failed = SimpleNamespace(
        id="bad", tool_call_id="bad", tool_name="read_server", arguments={}, arguments_hash=arguments_hash,
        risk="read", requires_approval=False, approved_by=None, approved_at=None, status="pending",
        result=None, error=None, progress_snapshot=None, completed_at=None,
    )
    await orchestrator._execute_tool_run(db, run, failed, user, None)
    assert failed.status == "failed" and "active" in failed.error

    approved = SimpleNamespace(
        id="approved", tool_call_id="approved", tool_name="write_server", arguments={}, arguments_hash="x",
        risk="write", requires_approval=True, approved_by=1, approved_at=datetime.now(timezone.utc),
        status="approved", result=None, error=None, progress_snapshot=None, completed_at=None,
    )
    db = _Session([user])
    await orchestrator._execute_tool_run(db, run, approved, user, None)
    assert approved.status == "failed" and "changed" in approved.error


@pytest.mark.asyncio
async def test_process_ai_run_waits_for_write_approval_and_handles_policy(monkeypatch):
    run = _run()
    conversation = SimpleNamespace(id="conv-1", user_id=1, updated_at=None)
    user = SimpleNamespace(id=1, is_admin=False, is_active=True)
    provider = SimpleNamespace(admin_prompt="")
    settings = SimpleNamespace(max_provider_rounds=2, max_tool_calls_per_round=2)
    db = _Session(
        [run, conversation, user],
        [
            _Rows(),
            _Rows(scalar=0),
            _Rows(scalar=0),
            _Rows(scalar=0),
            _Rows(),
            _Rows(),
        ],
    )
    monkeypatch.setattr(orchestrator, "async_session_maker", _SessionFactory(db))
    monkeypatch.setattr(orchestrator, "get_effective_provider", AsyncMock(return_value=provider))
    monkeypatch.setattr(orchestrator.AISystemSettings, "get_or_create", AsyncMock(return_value=settings))
    monkeypatch.setattr(orchestrator, "build_system_prompt", lambda *_args: "system")
    monkeypatch.setattr(
        orchestrator,
        "_create_provider_response_with_retry",
        AsyncMock(
            return_value={
                "content": "",
                "tool_calls": [
                    {"id": "call-1", "function": {"name": "apply_plugin_plan", "arguments": '{"plugin_id": 1, "expected_plan_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'} }
                ],
            }
        ),
    )
    monkeypatch.setattr(orchestrator, "build_approval_summary", AsyncMock(return_value={"steps": []}))
    monkeypatch.setattr(orchestrator, "_emit", AsyncMock())
    await orchestrator.process_ai_run(run.id)
    assert run.status == "waiting_approval"
    assert any(getattr(item, "status", None) == "pending_approval" for item in db.added)

    run = _run(server_id=4)
    conversation = SimpleNamespace(id="conv-1", user_id=1, updated_at=None)
    server = SimpleNamespace(id=4)
    db = _Session(
        [run, conversation, user],
        [_Rows(), _Rows(scalar=0), _Rows(scalar=0), _Rows(scalar=0), _Rows(), _Rows()],
    )
    monkeypatch.setattr(orchestrator, "async_session_maker", _SessionFactory(db))
    monkeypatch.setattr(orchestrator.Server, "get_by_id_and_user", AsyncMock(return_value=server))
    monkeypatch.setattr(orchestrator, "get_effective_provider", AsyncMock(return_value=provider))
    monkeypatch.setattr(orchestrator.AISystemSettings, "get_or_create", AsyncMock(return_value=settings))
    monkeypatch.setattr(
        orchestrator,
        "get_effective_agent_policy",
        AsyncMock(return_value=SimpleNamespace(enabled=False, capabilities=[])),
    )
    await orchestrator.process_ai_run(run.id)
    assert run.status == "failed" and "disabled" in run.error
