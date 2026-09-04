"""覆盖 AI 编排器的快照、令牌估算、重试和审批收口逻辑。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import ai_orchestrator as orchestrator
from services.ai_provider import AIProviderError


def test_approval_labels_snapshots_and_token_helpers(monkeypatch):
    assert orchestrator._approval_step_id("apply_plugin_plan", {"plugin_id": 3}, 0) == "plugin:3"
    assert (
        orchestrator._approval_step_id(
            "apply_workshop_map", {"action": "install_framework", "framework": "metamod"}, 0
        )
        == "install_metamod"
    )
    assert (
        orchestrator._approval_step_id("apply_workshop_map", {"action": "install_market_plugin"}, 1)
        == "install_mapchooser"
    )
    assert (
        orchestrator._approval_step_id("other", {"action": "restart_server"}, 0)
        == "step:1:restart_server"
    )
    assert orchestrator._approval_step_label("secret") == "secret"
    assert (
        orchestrator._approval_step_label({"action": "install_framework", "framework": "Metamod"})
        == "Install Metamod"
    )
    assert (
        orchestrator._approval_step_label({"action": "append_map", "name": "de_dust2"})
        == "Add map de_dust2"
    )
    summary = {
        "steps": [
            {"action": "install_market_plugin", "title": "X"},
            {"status": "already_installed", "plugin_id": 2, "title": "Y"},
        ]
    }
    plan, progress = orchestrator._build_plan_snapshots("apply_plugin_plan", summary)
    assert plan["version"] == 1 and progress["completed"] == 1
    tool_run = SimpleNamespace(progress_snapshot=progress, progress_updated_at=None)
    monkeypatch.setattr(orchestrator, "get_current_time", lambda: datetime.now(timezone.utc))
    orchestrator._update_progress_snapshot(
        tool_run,
        {"step_id": plan["steps"][0]["id"], "step_status": "running", "message": "working"},
    )
    orchestrator._update_progress_snapshot(
        tool_run, {"step_id": plan["steps"][0]["id"], "step_status": "completed", "message": "done"}
    )
    assert tool_run.progress_snapshot["completed"] == 2
    orchestrator._finalize_progress_snapshot(tool_run, success=False, message="failed")
    assert tool_run.progress_snapshot["message"] == "failed"
    orchestrator._finalize_progress_snapshot(
        SimpleNamespace(progress_snapshot=None), success=True, message="ok"
    )
    assert orchestrator._token_count(None) == 0
    assert orchestrator._token_count("abcd") == 1
    assert orchestrator._estimate_message_tokens([]) == 1
    assert orchestrator._estimate_response_tokens({"content": "abc", "tool_calls": []}) >= 1
    assert orchestrator._provider_token_usage(
        {"usage": {"prompt_tokens": "4", "completion_tokens": 5}}
    ) == (4, 5)
    assert orchestrator._provider_token_usage({"usage": {"total_tokens": 7}}) == (0, 7)
    assert orchestrator._provider_token_usage({}) is None
    assert orchestrator._retry_delay_seconds(2) == 30
    assert orchestrator._approval_is_expired(
        SimpleNamespace(approval_expires_at=None), datetime.now(timezone.utc)
    )
    assert orchestrator._approval_is_expired(
        SimpleNamespace(approval_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)),
        datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_delta_emitter_provider_retry_and_failure(monkeypatch):
    emitted = []
    monkeypatch.setattr(
        orchestrator, "_emit", AsyncMock(side_effect=lambda *args: emitted.append(args))
    )
    emitter = orchestrator._AssistantDeltaEmitter("run", 1, 3)
    await emitter.add("x" * 100)
    await emitter.flush()
    assert any(item[1] == "assistant_delta" for item in emitted)
    monkeypatch.setattr(
        orchestrator, "create_chat_completion", AsyncMock(return_value={"content": "ok"})
    )
    result = await orchestrator._create_provider_response_with_retry(
        SimpleNamespace(), [], run_id="run", round_index=1, server_selected=False
    )
    assert result["content"] == "ok"
    provider = AsyncMock(side_effect=[AIProviderError("temporary"), {"content": "recovered"}])
    monkeypatch.setattr(orchestrator, "create_chat_completion", provider)
    monkeypatch.setattr(orchestrator.asyncio, "sleep", AsyncMock())
    result = await orchestrator._create_provider_response_with_retry(
        SimpleNamespace(), [], run_id="run", round_index=2, server_selected=False
    )
    assert result["content"] == "recovered"
    provider.side_effect = AIProviderError("empty")
    with pytest.raises(AIProviderError):
        await orchestrator._create_provider_response_with_retry(
            SimpleNamespace(), [], run_id="run", round_index=3, server_selected=False
        )


class _Db:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.added = []
        self.deleted = []
        self.commits = 0

    async def execute(self, _statement):
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: self.rows), scalar_one_or_none=lambda: None
        )

    def add(self, item):
        self.added.append(item)

    async def delete(self, item):
        self.deleted.append(item)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_ai_approval_cleanup_and_lock_reconciliation(monkeypatch):
    run = SimpleNamespace(
        id="run", conversation_id="conversation", status="waiting_approval", completed_at=None
    )
    expired = SimpleNamespace(
        id="t1",
        status="pending_approval",
        approval_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        risk="write",
        result=None,
        completed_at=None,
        tool_call_id="call",
        tool_name="tool",
        progress_snapshot=None,
    )
    cancelled = SimpleNamespace(
        id="t2",
        status="approved",
        approval_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        risk="read",
        result=None,
        completed_at=None,
        tool_call_id="call2",
        tool_name="tool2",
        progress_snapshot=None,
    )
    db = _Db([run])
    await orchestrator._close_unexecuted_tools(db, run, [expired, cancelled], expired_ids={"t1"})
    assert expired.status == "expired" and cancelled.status == "cancelled"
    assert len(db.added) >= 4
    db = _Db([])
    assert await orchestrator.reconcile_waiting_approval_runs(db) == set()
    old = SimpleNamespace(id="old")
    db = _Db([old])
    assert await orchestrator.cleanup_expired_ai_runs(db) == 1
    monkeypatch.setattr(
        orchestrator.maintenance_lock_service,
        "clear_stale_server_lock",
        AsyncMock(return_value=True),
    )
    assert await orchestrator.reconcile_stale_ai_server_lock(db, None) is False
    assert await orchestrator.reconcile_stale_ai_server_lock(db, 3) is True
    with pytest.raises(AIProviderError):
        orchestrator._validate_write_tool_batch(["apply_plugin_plan", "apply_workshop_map"])
    orchestrator._validate_write_tool_batch(["inspect_server", "tail_server_log"])

    run_db = _Db()
    run_obj = SimpleNamespace(
        id="r", conversation_id="c", status="running", error=None, completed_at=None
    )
    monkeypatch.setattr(orchestrator, "_emit", AsyncMock())
    await orchestrator._fail_run(run_db, run_obj, "password=secret")
    assert run_obj.status == "failed" and run_db.commits == 1
