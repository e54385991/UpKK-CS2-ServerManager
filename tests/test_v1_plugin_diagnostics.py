"""Coverage for the versioned ``/api/v1/servers/{id}/plugin-diagnostics`` contract."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

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

    async def stale(*_args, **_kwargs):
        raise ValueError("Plugin plan changed; review and approve the new plan")

    monkeypatch.setattr(
        "api.routes.v1.plugin_diagnostics.execute_diagnostic_plan",
        stale,
    )
    response = client.post(
        "/api/v1/servers/2/plugin-diagnostics/runs",
        json={"scope": "both", "expected_plan_hash": "b" * 64},
    )
    assert response.status_code == 409
