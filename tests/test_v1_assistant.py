"""Coverage for the versioned ``/api/v1/assistant`` workspace."""

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


def test_v1_assistant_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/assistant")
    assert response.status_code == 401


def test_v1_assistant_workspace_when_provider_is_off(monkeypatch):
    client, _user = _client(monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.assistant.legacy.get_user_ai_settings",
        AsyncMock(
            return_value=SimpleNamespace(
                effective_enabled=False,
                effective_source="none",
                model=None,
            )
        ),
    )
    monkeypatch.setattr(
        "api.routes.v1.assistant.legacy.list_ai_conversations",
        AsyncMock(return_value=[]),
    )

    response = client.get("/api/v1/assistant")
    assert response.status_code == 200
    body = response.json()
    assert body["provider_ready"] is False
    assert body["mode"] == "none"
    assert body["conversations"] == []


def test_v1_assistant_workspace_when_provider_is_ready(monkeypatch):
    client, _user = _client(monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.assistant.legacy.get_user_ai_settings",
        AsyncMock(
            return_value=SimpleNamespace(
                effective_enabled=True,
                effective_source="global",
                model=None,
            )
        ),
    )
    monkeypatch.setattr(
        "api.routes.v1.assistant.legacy.list_ai_conversations",
        AsyncMock(return_value=[]),
    )

    response = client.get("/api/v1/assistant")
    assert response.status_code == 200
    body = response.json()
    assert body["provider_ready"] is True
    assert body["mode"] == "global"
    assert body["conversations"] == []
