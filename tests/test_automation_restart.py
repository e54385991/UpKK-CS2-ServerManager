"""Restart admission and its final check never stop a newly occupied server."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes.actions import deployment, deployment_executor, lifecycle_actions
from modules import ServerAction
from services.automation.commands import AutomationDenied


async def test_final_restart_check_blocks_stop_after_preflight(monkeypatch):
    ssh = SimpleNamespace(stop_server=AsyncMock(), start_server=AsyncMock())
    ctx = SimpleNamespace(
        server_id=12,
        server=SimpleNamespace(),
        ssh_manager=ssh,
        log=SimpleNamespace(status="in_progress"),
        progress_callback=AsyncMock(),
        clear_crash_protection=AsyncMock(),
        clear_execstack=False,
        clear_execstack_targets=None,
        automation_guard=True,
    )
    monkeypatch.setattr(deployment, "send_deployment_update", AsyncMock())
    probe = AsyncMock(side_effect=AutomationDenied("The server has human players."))
    monkeypatch.setattr(lifecycle_actions, "ensure_eligible", probe)
    success, message = await lifecycle_actions._action_restart(ctx)
    assert success is False
    assert "human players" in message
    assert ctx.log.status == "failed"
    ssh.stop_server.assert_not_awaited()
    ssh.start_server.assert_not_awaited()


async def test_restart_admission_precedes_lifecycle_intent_and_side_effects(monkeypatch):
    target = SimpleNamespace(manual_stop_requested=True)
    monkeypatch.setattr(
        deployment, "get_server_and_verify_ownership", AsyncMock(return_value=target)
    )
    probe = AsyncMock(side_effect=AutomationDenied("Administrator stop request."))
    monkeypatch.setattr(deployment_executor, "ensure_eligible", probe)
    intent = AsyncMock()
    monkeypatch.setattr(deployment, "apply_user_lifecycle_intent", intent)
    db = SimpleNamespace(commit=AsyncMock(), add=AsyncMock())
    with pytest.raises(AutomationDenied):
        await deployment_executor.execute_server_action(
            12,
            ServerAction(action="restart"),
            db,
            SimpleNamespace(id=7),
            None,
            automation_guard=True,
        )
    db.commit.assert_awaited_once()
    db.add.assert_not_called()
    intent.assert_not_awaited()
