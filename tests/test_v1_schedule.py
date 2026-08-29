"""Coverage for the versioned ``/api/v1/servers/{id}/schedule`` workspace."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _client(monkeypatch):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="owner", is_admin=False, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app), user


def _task(**overrides):
    values = {
        "id": 9,
        "server_id": 2,
        "name": "nightly restart",
        "action": "restart",
        "enabled": True,
        "schedule_type": "daily",
        "schedule_value": "03:00",
        "last_run": None,
        "next_run": None,
        "run_count": 0,
        "last_status": None,
        "last_error": None,
        "created_at": None,
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_v1_schedule_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/servers/2/schedule")
    assert response.status_code == 401


def test_v1_schedule_list_and_create(monkeypatch):
    client, _user = _client(monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.schedule.legacy.list_scheduled_tasks",
        AsyncMock(return_value=[_task()]),
    )
    listed = client.get("/api/v1/servers/2/schedule")
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "nightly restart"
    assert listed.json()[0]["action"] == "restart"

    monkeypatch.setattr(
        "api.routes.v1.schedule.legacy.create_scheduled_task",
        AsyncMock(return_value=_task(id=10, name="backup")),
    )
    created = client.post(
        "/api/v1/servers/2/schedule",
        json={
            "name": "backup",
            "action": "backup_plugins",
            "enabled": True,
            "schedule_type": "daily",
            "schedule_value": "04:00",
        },
    )
    assert created.status_code == 200
    assert created.json()["id"] == 10
