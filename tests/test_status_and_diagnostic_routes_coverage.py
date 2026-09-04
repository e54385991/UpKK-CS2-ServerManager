"""覆盖状态回报和插件诊断路由的鉴权、冲突及成功映射。"""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes import plugin_diagnostics as diagnostics
from api.routes import server_status as status_routes
from modules import ServerStatus
from services.ai_access import AgentAccessDenied


class _Db:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


def _server(server_id=4):
    return SimpleNamespace(
        id=server_id,
        name="Alpha",
        game_port=27015,
        default_map="de_dust2",
        max_players=16,
        game_mode="competitive",
        game_type="0",
        status=ServerStatus.STOPPED,
    )


@pytest.mark.asyncio
async def test_server_status_verifier_and_all_event_state_transitions(monkeypatch):
    db = _Db()
    server = _server()
    monkeypatch.setattr(status_routes.Server, "get_by_api_key", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc_info:
        await status_routes.verify_server_api_key("bad", db=db)
    assert exc_info.value.status_code == 401
    monkeypatch.setattr(status_routes.Server, "get_by_api_key", AsyncMock(return_value=server))
    assert await status_routes.verify_server_api_key("good", db=db) is server

    with pytest.raises(HTTPException) as exc_info:
        await status_routes.report_server_status(99, status_routes.ServerStatusReport(event_type="crash"), server, db=db)
    assert exc_info.value.status_code == 403

    expected = {
        "crash": ServerStatus.ERROR,
        "restart": ServerStatus.RUNNING,
        "startup": ServerStatus.RUNNING,
        "shutdown": ServerStatus.STOPPED,
        "crash_limit_reached": ServerStatus.STOPPED,
        "unknown": ServerStatus.STOPPED,
    }
    for event_type, state in expected.items():
        server.status = ServerStatus.STOPPED
        report = status_routes.ServerStatusReport(
            event_type=event_type,
            message=None if event_type != "crash" else "crashed",
            restart_count=3,
            crash_details="trace" if event_type == "crash" else None,
        )
        response = await status_routes.report_server_status(4, report, server, db=db)
        assert response["current_status"] == state.value
        assert server.status == state
    assert db.commits == len(expected)
    assert db.added[-1].action == "auto_unknown"
    assert db.added[0].error_message == "trace"
    assert "excessive crashes" in db.added[4].error_message


@pytest.mark.asyncio
async def test_server_config_and_pool_stats(monkeypatch):
    db = _Db()
    server = _server()
    config = await status_routes.get_server_config(4, server, db=db)
    assert config == {
        "server_id": 4,
        "name": "Alpha",
        "game_port": 27015,
        "default_map": "de_dust2",
        "max_players": 16,
        "game_mode": "competitive",
        "game_type": "0",
    }
    with pytest.raises(HTTPException) as exc_info:
        await status_routes.get_server_config(5, server, db=db)
    assert exc_info.value.status_code == 403
    pool = SimpleNamespace(get_pool_stats=AsyncMock(return_value={"active": 2}))
    pool_module = import_module("services.ssh_connection_pool")
    monkeypatch.setattr(pool_module, "ssh_connection_pool", pool)
    assert await status_routes.get_ssh_pool_stats() == {"success": True, "pool_stats": {"active": 2}}


def _user():
    return SimpleNamespace(id=7)


@pytest.mark.asyncio
async def test_plugin_diagnostic_routes_map_success_and_access_errors(monkeypatch):
    db = object()
    user = _user()
    monkeypatch.setattr(diagnostics, "enforce_agent_rate_limit", AsyncMock())
    monkeypatch.setattr(diagnostics, "get_diagnostic_recommendation", AsyncMock(return_value={"hint": "x"}))
    assert await diagnostics.read_plugin_diagnostic_recommendation(3, db, user) == {"hint": "x"}

    monkeypatch.setattr(diagnostics, "build_diagnostic_plan", AsyncMock(return_value={"plan": []}))
    request = SimpleNamespace(scope="all")
    assert await diagnostics.plan_plugin_diagnostic(3, request, db, user) == {"plan": []}
    diagnostics.enforce_agent_rate_limit.assert_awaited_once_with(7, "diagnostic_plan", limit=10)

    execute = AsyncMock(return_value={"id": "run"})
    monkeypatch.setattr(diagnostics, "execute_diagnostic_plan", execute)
    run_request = SimpleNamespace(scope="all", expected_plan_hash="hash")
    assert await diagnostics.run_plugin_diagnostic(3, run_request, db, user) == {"id": "run"}
    execute.assert_awaited_once_with(db, user, 3, "all", "hash")

    monkeypatch.setattr(diagnostics, "get_diagnostic_run", AsyncMock(return_value={"status": "failed"}))
    monkeypatch.setattr(diagnostics, "restore_diagnostic_run", AsyncMock(return_value={"id": "restored"}))
    assert await diagnostics.read_plugin_diagnostic(3, "d", db, user) == {"status": "failed"}
    assert await diagnostics.restore_plugin_diagnostic(3, "d", db, user) == {"id": "restored"}
    monkeypatch.setattr(diagnostics, "execute_diagnostic_plan", AsyncMock(return_value={"id": "resumed"}))
    assert await diagnostics.resume_plugin_diagnostic(3, "d", run_request, db, user) == {"id": "resumed"}

    monkeypatch.setattr(diagnostics, "get_diagnostic_recommendation", AsyncMock(side_effect=AgentAccessDenied("hidden")))
    with pytest.raises(HTTPException) as exc_info:
        await diagnostics.read_plugin_diagnostic_recommendation(3, db, user)
    assert exc_info.value.status_code == 404
    monkeypatch.setattr(diagnostics, "build_diagnostic_plan", AsyncMock(side_effect=AgentAccessDenied("hidden")))
    with pytest.raises(HTTPException) as exc_info:
        await diagnostics.plan_plugin_diagnostic(3, request, db, user)
    assert exc_info.value.status_code == 404
    monkeypatch.setattr(diagnostics, "execute_diagnostic_plan", AsyncMock(side_effect=ValueError("stale plan")))
    with pytest.raises(HTTPException) as exc_info:
        await diagnostics.run_plugin_diagnostic(3, run_request, db, user)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_plugin_diagnostic_read_restore_resume_error_matrix(monkeypatch):
    db = object()
    user = _user()
    monkeypatch.setattr(diagnostics, "get_diagnostic_run", AsyncMock(side_effect=LookupError("missing")))
    with pytest.raises(HTTPException) as exc_info:
        await diagnostics.read_plugin_diagnostic(3, "missing", db, user)
    assert exc_info.value.status_code == 404
    monkeypatch.setattr(diagnostics, "restore_diagnostic_run", AsyncMock(side_effect=LookupError("missing")))
    with pytest.raises(HTTPException) as exc_info:
        await diagnostics.restore_plugin_diagnostic(3, "missing", db, user)
    assert exc_info.value.status_code == 404

    monkeypatch.setattr(diagnostics, "get_diagnostic_run", AsyncMock(return_value={"status": "running"}))
    with pytest.raises(HTTPException) as exc_info:
        await diagnostics.resume_plugin_diagnostic(3, "run", SimpleNamespace(scope="x", expected_plan_hash="h"), db, user)
    assert exc_info.value.status_code == 409
    monkeypatch.setattr(diagnostics, "get_diagnostic_run", AsyncMock(side_effect=AgentAccessDenied("gone")))
    with pytest.raises(HTTPException) as exc_info:
        await diagnostics.resume_plugin_diagnostic(3, "run", SimpleNamespace(scope="x", expected_plan_hash="h"), db, user)
    assert exc_info.value.status_code == 404
