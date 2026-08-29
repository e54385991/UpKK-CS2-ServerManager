"""Coverage for versioned fleet batch actions and owner-only authorization."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.application import create_app
from api.dependencies import get_bearer_or_cookie_user
from modules import get_current_active_user, get_current_user, get_db


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _client(*, admin: bool = False):
    app = create_app(lifespan=None)
    user = SimpleNamespace(
        id=1,
        username="admin" if admin else "owner",
        is_admin=admin,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_bearer_or_cookie_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app), user


def test_v1_batch_actions_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.post(
        "/api/v1/servers/batch-actions",
        json={"server_ids": [1], "action": "restart"},
    )
    assert response.status_code == 401


def test_v1_batch_actions_returns_202_for_owned_servers(monkeypatch):
    client, _user = _client()
    monkeypatch.setattr(
        "api.routes.v1.batch.authorized_server_ids",
        AsyncMock(return_value=[1, 3]),
    )
    monkeypatch.setattr(
        "api.routes.v1.batch.redis_manager.set_batch_action_meta",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "api.routes.v1.batch.redis_manager.set_batch_action_statuses",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr("api.routes.v1.batch._reserve_batch_capacity", AsyncMock())
    monkeypatch.setattr("api.routes.v1.batch._store_task", lambda _task: None)

    def _fake_create_task(coro):
        coro.close()
        return SimpleNamespace(cancel=lambda: None)

    monkeypatch.setattr("api.routes.v1.batch.asyncio.create_task", _fake_create_task)
    monkeypatch.setattr(
        "api.routes.v1.batch.record_audit_event",
        AsyncMock(return_value=None),
    )
    response = client.post(
        "/api/v1/servers/batch-actions",
        json={"server_ids": [1, 2, 3], "action": "restart"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["server_count"] == 2
    assert body["accepted_server_ids"] == [1, 3]
    assert body["action"] == "restart"
    assert body["batch_id"]
    assert body["stream_url"].endswith(f"/batch-actions/{body['batch_id']}/events")


def test_v1_batch_actions_rejects_unowned_servers(monkeypatch):
    client, _user = _client(admin=True)
    monkeypatch.setattr(
        "api.routes.v1.batch.authorized_server_ids",
        AsyncMock(return_value=[]),
    )
    response = client.post(
        "/api/v1/servers/batch-actions",
        json={"server_ids": [2], "action": "stop"},
    )
    assert response.status_code == 400


def test_v1_batch_journal_is_actor_only(monkeypatch):
    client, _user = _client()
    monkeypatch.setattr(
        "api.routes.v1.batch.redis_manager.get_batch_action_meta",
        AsyncMock(return_value={"actor_user_id": 9, "action": "restart"}),
    )
    response = client.get("/api/v1/servers/batch-actions/deadbeef")
    assert response.status_code == 404


def test_v1_batch_journal_returns_summary(monkeypatch):
    client, _user = _client()
    monkeypatch.setattr(
        "api.routes.v1.batch.redis_manager.get_batch_action_meta",
        AsyncMock(return_value={"actor_user_id": 1, "action": "restart"}),
    )
    monkeypatch.setattr(
        "api.routes.v1.batch.redis_manager.get_batch_action_status",
        AsyncMock(
            return_value={
                "1": {"status": "success", "message": "ok"},
                "3": {"status": "in_progress", "message": "working"},
            }
        ),
    )
    response = client.get("/api/v1/servers/batch-actions/abc123")
    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "restart"
    assert body["summary"]["total"] == 2
    assert body["summary"]["succeeded"] == 1
    assert body["summary"]["is_complete"] is False
