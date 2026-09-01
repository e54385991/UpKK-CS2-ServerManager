"""Coverage for the versioned ``/api/v1/servers/{id}/plugin-diagnostics`` contract."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db
from services.ai_access import AgentAccessDenied


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _client():
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="owner", is_admin=False, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app), user


def test_v1_plugin_diagnostics_require_authentication():
    client = TestClient(create_app(lifespan=None))
    assert client.get("/api/v1/servers/2/plugin-diagnostics/recommendation").status_code == 401
    assert (
        client.post("/api/v1/servers/2/plugin-diagnostics/plan", json={"scope": "both"}).status_code
        == 401
    )


def test_v1_plugin_diagnostics_recommendation(monkeypatch):
    client, _user = _client()
    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.get_diagnostic_recommendation",
        AsyncMock(
            return_value={
                "recommended": True,
                "reason": "post_update_start_failures",
                "recently_updated": True,
                "last_update_time": None,
                "restart_count": 3,
                "max_restarts": 5,
                "window_minutes": 30,
            }
        ),
    )
    response = client.get("/api/v1/servers/2/plugin-diagnostics/recommendation")
    assert response.status_code == 200
    body = response.json()
    assert body["recommended"] is True
    assert body["reason"] == "post_update_start_failures"
    assert body["restart_count"] == 3


def test_v1_plugin_diagnostics_plan_and_hidden_for_non_owner(monkeypatch):
    client, _user = _client()
    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.enforce_agent_rate_limit",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.build_diagnostic_plan",
        AsyncMock(
            return_value={
                "server_id": 2,
                "scope": "both",
                "plan_hash": "a" * 64,
                "candidates": [{"key": "css"}],
                "candidate_groups": [],
                "estimated_max_starts": 4,
                "health_policy": {},
                "warnings": [],
            }
        ),
    )
    planned = client.post("/api/v1/servers/2/plugin-diagnostics/plan", json={"scope": "both"})
    assert planned.status_code == 200
    assert planned.json()["plan_hash"] == "a" * 64

    async def deny(*_args, **_kwargs):
        raise AgentAccessDenied("Server not found")

    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.build_diagnostic_plan",
        deny,
    )
    hidden = client.post("/api/v1/servers/9/plugin-diagnostics/plan", json={"scope": "both"})
    assert hidden.status_code == 404


def test_v1_plugin_diagnostics_execute_conflict(monkeypatch):
    client, _user = _client()
    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.enforce_agent_rate_limit",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.build_diagnostic_plan",
        AsyncMock(
            return_value={
                "server_id": 2,
                "scope": "both",
                "plan_hash": "a" * 64,
                "candidates": [],
                "candidate_groups": [],
                "estimated_max_starts": 4,
                "health_policy": {},
                "warnings": [],
            }
        ),
    )
    enqueue = AsyncMock()
    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.enqueue_plugin_diagnostic_execute",
        enqueue,
    )
    response = client.post(
        "/api/v1/servers/2/plugin-diagnostics/runs",
        json={"scope": "both", "expected_plan_hash": "b" * 64},
    )
    assert response.status_code == 409
    enqueue.assert_not_awaited()


def _queued_operation(operation_id: str, *, action: str, user_id: int = 1):
    return {
        "operation_id": operation_id,
        "server_id": 2,
        "action": action,
        "status": "queued",
        "success": None,
        "message": None,
        "server_status": None,
        "actor_user_id": user_id,
        "started_at": "2026-09-01T00:00:00+00:00",
        "completed_at": None,
    }


def _matching_plan():
    return {
        "server_id": 2,
        "scope": "both",
        "plan_hash": "a" * 64,
        "candidates": [],
        "candidate_groups": [],
        "estimated_max_starts": 4,
        "health_policy": {},
        "warnings": [],
    }


def test_v1_plugin_diagnostics_execute_returns_202(monkeypatch):
    client, _user = _client()
    operation_id = str(uuid4())
    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.enforce_agent_rate_limit",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.build_diagnostic_plan",
        AsyncMock(return_value=_matching_plan()),
    )
    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.reject_stuck_lock_unless_active",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.enqueue_plugin_diagnostic_execute",
        AsyncMock(return_value=_queued_operation(operation_id, action="plugin_diagnostic_execute")),
    )
    response = client.post(
        "/api/v1/servers/2/plugin-diagnostics/runs",
        json={"scope": "both", "expected_plan_hash": "a" * 64},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["operation_id"] == operation_id
    assert body["action"] == "plugin_diagnostic_execute"
    assert body["stream_url"] == f"/api/v1/servers/2/operations/{operation_id}/events"


def test_v1_plugin_diagnostics_restore_returns_202(monkeypatch):
    client, _user = _client()
    operation_id = str(uuid4())
    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.get_diagnostic_run",
        AsyncMock(
            return_value={
                "id": "diag-1",
                "server_id": 2,
                "requested_by": 1,
                "scope": "both",
                "status": "completed",
                "plan_hash": "a" * 64,
            }
        ),
    )
    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.reject_stuck_lock_unless_active",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.enqueue_plugin_diagnostic_restore",
        AsyncMock(return_value=_queued_operation(operation_id, action="plugin_diagnostic_restore")),
    )
    response = client.post("/api/v1/servers/2/plugin-diagnostics/runs/diag-1/restore")
    assert response.status_code == 202
    body = response.json()
    assert body["operation_id"] == operation_id
    assert body["action"] == "plugin_diagnostic_restore"


def test_v1_plugin_diagnostics_resume_returns_202(monkeypatch):
    client, _user = _client()
    operation_id = str(uuid4())
    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.get_diagnostic_run",
        AsyncMock(
            return_value={
                "id": "diag-1",
                "server_id": 2,
                "requested_by": 1,
                "scope": "both",
                "status": "interrupted",
                "plan_hash": "a" * 64,
            }
        ),
    )
    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.reject_stuck_lock_unless_active",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.enqueue_plugin_diagnostic_resume",
        AsyncMock(return_value=_queued_operation(operation_id, action="plugin_diagnostic_resume")),
    )
    response = client.post(
        "/api/v1/servers/2/plugin-diagnostics/runs/diag-1/resume",
        json={"scope": "both", "expected_plan_hash": "a" * 64},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["operation_id"] == operation_id
    assert body["action"] == "plugin_diagnostic_resume"


def test_v1_plugin_diagnostics_latest_run(monkeypatch):
    client, _user = _client()
    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.get_latest_diagnostic_run",
        AsyncMock(
            return_value={
                "id": "diag-1",
                "server_id": 2,
                "requested_by": 1,
                "scope": "both",
                "status": "completed",
                "plan_hash": "a" * 64,
                "culprit_keys": [],
                "start_attempts": 2,
                "error": None,
                "steps": [],
                "quarantine": [],
                "created_at": "2026-09-01T00:00:00+00:00",
                "completed_at": "2026-09-01T00:10:00+00:00",
            }
        ),
    )
    found = client.get("/api/v1/servers/2/plugin-diagnostics/latest-run")
    assert found.status_code == 200
    assert found.json()["id"] == "diag-1"
    assert found.json()["status"] == "completed"

    async def missing(*_args, **_kwargs):
        raise LookupError("Diagnostic run not found")

    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.get_latest_diagnostic_run",
        missing,
    )
    missing_response = client.get("/api/v1/servers/2/plugin-diagnostics/latest-run")
    assert missing_response.status_code == 404
