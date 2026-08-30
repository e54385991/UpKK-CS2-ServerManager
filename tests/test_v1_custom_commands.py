"""Coverage for the versioned ``/api/v1/servers/{id}/custom-commands`` contract."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db


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
    return TestClient(app)


def _command(**overrides):
    values = {
        "id": 4,
        "user_id": 1,
        "server_id": 7,
        "name": "clear execstack",
        "target": "host",
        "commands": "patchelf --clear-execstack /opt/cs2/plugin.so",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_v1_custom_commands_require_authentication():
    client = TestClient(create_app(lifespan=None))
    assert client.get("/api/v1/servers/7/custom-commands").status_code == 401


def test_v1_custom_commands_list_and_create(monkeypatch):
    client = _client()
    monkeypatch.setattr(
        "api.routes.v1.custom_commands.legacy.list_custom_commands",
        AsyncMock(return_value=[_command()]),
    )
    listed = client.get("/api/v1/servers/7/custom-commands")
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "clear execstack"
    assert listed.json()[0]["target"] == "host"
    assert "ssh_password" not in listed.json()[0]

    monkeypatch.setattr(
        "api.routes.v1.custom_commands.legacy.create_custom_command",
        AsyncMock(return_value=_command(id=5, name="status")),
    )
    created = client.post(
        "/api/v1/servers/7/custom-commands",
        json={"name": "status", "target": "game_process", "commands": "status"},
    )
    assert created.status_code == 201
    assert created.json()["id"] == 5
    assert created.json()["name"] == "status"


def test_v1_custom_commands_execute_projects_log(monkeypatch):
    client = _client()
    response_body = SimpleNamespace(
        success=True,
        message="Command completed",
        data={
            "target": "host",
            "results": [
                {
                    "index": 1,
                    "command": "uname -s",
                    "success": True,
                    "stdout": "Linux",
                    "stderr": "",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "api.routes.v1.custom_commands.legacy.execute_saved_custom_command",
        AsyncMock(return_value=response_body),
    )
    response = client.post("/api/v1/servers/7/custom-commands/4/execute")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "Linux" in body["log"]
    assert "uname -s" in body["log"]
