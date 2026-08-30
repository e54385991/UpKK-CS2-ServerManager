"""Coverage for the versioned ``/api/v1/discord`` bot settings."""

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


def test_v1_discord_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/discord")
    assert response.status_code == 401


def test_v1_discord_get_projects_unconfigured_bot(monkeypatch):
    client, _user = _client(monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.discord.legacy.get_discord_bot",
        AsyncMock(
            return_value=SimpleNamespace(
                enabled=False,
                token_configured=False,
                message_trigger_mode="mention_only",
                username=None,
                connection_status="not_configured",
                last_error=None,
                invite_url=None,
            )
        ),
    )

    response = client.get("/api/v1/discord")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["token_configured"] is False
    assert body["connection_status"] == "not_configured"


def test_v1_discord_global_binding_projects_defaults(monkeypatch):
    client, _user = _client(monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.discord.legacy.get_discord_global_binding",
        AsyncMock(
            return_value=SimpleNamespace(
                configured=False,
                enabled=False,
                guild_id=None,
                channel_ids=[],
                role_ids=[],
                user_ids=[],
                allow_channel_managers=False,
                allow_server_administrators=False,
                capabilities=[],
                server_count=2,
                matching_server_count=0,
                synced_server_count=0,
                inherited_by_new_servers=True,
            )
        ),
    )
    response = client.get("/api/v1/discord/global")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["server_count"] == 2
    assert body["capabilities"] == []


def test_v1_discord_options_degrade_without_token(monkeypatch):
    from fastapi import HTTPException

    client, _user = _client(monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.discord.legacy.get_discord_global_binding_options",
        AsyncMock(
            side_effect=HTTPException(status_code=409, detail="Discord Bot Token is not configured")
        ),
    )
    response = client.get("/api/v1/discord/global/options")
    assert response.status_code == 200
    body = response.json()
    assert body["token_configured"] is False
    assert body["guilds"] == []
    assert "not configured" in (body["message"] or "")


def test_v1_discord_menu_options_degrade_when_discord_api_fails(monkeypatch):
    from fastapi import HTTPException

    client, _user = _client(monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.discord.legacy.get_discord_menu_push_options",
        AsyncMock(side_effect=HTTPException(status_code=400, detail="Discord API request failed")),
    )
    response = client.get("/api/v1/discord/menu/options")
    assert response.status_code == 200
    body = response.json()
    assert body["token_configured"] is True
    assert body["guilds"] == []
    assert "Discord API" in (body["message"] or "")


def test_v1_server_discord_and_agent_policy(monkeypatch):
    client, _user = _client(monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.discord_servers.legacy.get_server_discord_bot_settings",
        AsyncMock(
            return_value=SimpleNamespace(
                server_id=2,
                enabled=False,
                effective_enabled=False,
                disabled_reason="token_missing",
                guild_id=None,
                channel_ids=[],
                role_ids=[],
                user_ids=[],
                allow_channel_managers=False,
                allow_server_administrators=False,
                capabilities=[],
                response_visibility="public",
            )
        ),
    )
    monkeypatch.setattr(
        "api.routes.v1.discord_servers.legacy.get_server_agent_policy",
        AsyncMock(
            return_value=SimpleNamespace(
                server_id=2,
                enabled=True,
                effective_enabled=True,
                disabled_reason=None,
                capabilities=["inspect_status", "read_logs_files"],
            )
        ),
    )
    binding = client.get("/api/v1/servers/2/discord")
    assert binding.status_code == 200
    assert binding.json()["server_id"] == 2
    assert binding.json()["disabled_reason"] == "token_missing"

    policy = client.get("/api/v1/servers/2/agent-policy")
    assert policy.status_code == 200
    assert policy.json()["capabilities"] == ["inspect_status", "read_logs_files"]
