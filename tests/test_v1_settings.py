"""Coverage for the versioned ``/api/v1/settings`` admin contract."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.application import create_app
from modules import (
    SystemSettings,
    get_current_active_user,
    get_current_admin_user,
    get_current_user,
    get_db,
)
from modules.schemas import GmailCredentialsUploadRequest
from services.client_ip import cached_client_ip_header, reset_client_ip_header_cache


@pytest.fixture(autouse=True)
def _restore_client_ip_policy():
    yield
    reset_client_ip_header_cache()


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _sample_settings(**overrides) -> SystemSettings:
    values = {
        "id": 1,
        "default_proxy_mode": "panel",
        "github_proxy_url": "https://ghfast.top",
        "captcha_enabled": True,
        "global_github_token": "github_pat_secret123456",
        "email_enabled": True,
        "email_provider": "smtp",
        "email_from_address": "noreply@example.com",
        "email_from_name": "CS2 Server Manager",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "relay",
        "smtp_password": "smtp-secret",
        "smtp_use_tls": True,
        "gmail_credentials_json": '{"web": {"client_id": "abc"}}',
        "gmail_token_json": '{"token": "hidden"}',
    }
    values.update(overrides)
    return SystemSettings(**values)


def _client(*, admin: bool = True, settings: SystemSettings | None = None, monkeypatch=None):
    app = create_app(lifespan=None)
    user = SimpleNamespace(
        id=1,
        username="admin" if admin else "member",
        is_admin=admin,
        is_active=True,
        email="admin@example.com",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    if admin:
        app.dependency_overrides[get_current_admin_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db

    row = settings or _sample_settings()
    if monkeypatch is not None:
        monkeypatch.setattr(
            "api.routes.v1.settings.SystemSettings.get_or_create_settings",
            AsyncMock(return_value=row),
        )
        monkeypatch.setattr("api.routes.v1.settings.record_audit_event", AsyncMock())
    return TestClient(app), row, user


def test_v1_settings_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/settings")
    assert response.status_code == 401


def test_v1_settings_rejects_non_admin():
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=2, username="member", is_admin=False, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    client = TestClient(app)
    response = client.get("/api/v1/settings")
    assert response.status_code == 403


def test_v1_settings_get_exposes_presence_flags_not_secrets(monkeypatch):
    client, _row, _user = _client(monkeypatch=monkeypatch)
    response = client.get("/api/v1/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["has_global_github_token"] is True
    assert body["global_github_token_prefix"] == "github_pat_s..."
    assert body["has_smtp_password"] is True
    assert body["has_gmail_credentials"] is True
    assert body["has_gmail_token"] is True
    assert body["gmail_ready"] is True
    assert body["default_proxy_mode"] == "panel"
    assert body["captcha_enabled"] is True
    dumped = response.text
    assert "github_pat_secret123456" not in dumped
    assert "smtp-secret" not in dumped
    assert "hidden" not in dumped
    assert "client_id" not in dumped
    for secret_key in (
        "global_github_token",
        "smtp_password",
        "gmail_credentials_json",
        "gmail_token_json",
    ):
        assert secret_key not in body


def test_v1_settings_put_keeps_token_when_omitted(monkeypatch):
    client, settings, _user = _client(monkeypatch=monkeypatch)
    response = client.put(
        "/api/v1/settings",
        json={"email_from_name": "Ops Console", "global_github_token": None},
    )
    assert response.status_code == 200
    assert settings.email_from_name == "Ops Console"
    assert settings.global_github_token == "github_pat_secret123456"
    assert settings.smtp_password == "smtp-secret"
    assert "global_github_token" not in response.json()
    assert "smtp_password" not in response.json()


def test_v1_settings_put_sets_and_clears_token(monkeypatch):
    client, settings, _user = _client(monkeypatch=monkeypatch)
    set_response = client.put(
        "/api/v1/settings",
        json={"global_github_token": "ghp_replacementtoken1"},
    )
    assert set_response.status_code == 200
    assert settings.global_github_token == "ghp_replacementtoken1"
    assert set_response.json()["has_global_github_token"] is True
    assert "ghp_replacementtoken1" not in set_response.text

    clear_response = client.put("/api/v1/settings", json={"clear_global_github_token": True})
    assert clear_response.status_code == 200
    assert settings.global_github_token is None
    assert clear_response.json()["has_global_github_token"] is False


def test_v1_settings_put_updates_captcha_policy(monkeypatch):
    client, settings, _user = _client(monkeypatch=monkeypatch)
    response = client.put("/api/v1/settings", json={"captcha_enabled": False})
    assert response.status_code == 200
    assert settings.captcha_enabled is False
    assert response.json()["captcha_enabled"] is False


def test_v1_settings_put_updates_client_ip_header(monkeypatch):
    client, settings, _user = _client(monkeypatch=monkeypatch)
    assert settings.client_ip_header == "X-Forwarded-For"

    response = client.put("/api/v1/settings", json={"client_ip_header": " CF-Connecting-IP "})
    assert response.status_code == 200
    assert settings.client_ip_header == "CF-Connecting-IP"
    assert response.json()["client_ip_header"] == "CF-Connecting-IP"
    assert cached_client_ip_header() == "CF-Connecting-IP"

    cleared = client.put("/api/v1/settings", json={"client_ip_header": ""})
    assert cleared.status_code == 200
    assert settings.client_ip_header is None
    assert cleared.json()["client_ip_header"] is None
    assert cached_client_ip_header() is None


def test_v1_settings_put_rejects_an_invalid_client_ip_header(monkeypatch):
    client, settings, _user = _client(monkeypatch=monkeypatch)
    response = client.put("/api/v1/settings", json={"client_ip_header": "X Forwarded For"})
    assert response.status_code == 422
    assert settings.client_ip_header == "X-Forwarded-For"


def test_v1_settings_put_updates_console_log_level(monkeypatch):
    applied: list[str | None] = []
    monkeypatch.setattr(
        "api.routes.v1.settings.apply_console_log_level",
        lambda value: applied.append(value) or "WARNING",
    )
    client, settings, _user = _client(monkeypatch=monkeypatch)
    assert settings.log_level == "ERROR"

    response = client.put("/api/v1/settings", json={"log_level": "warning"})
    assert response.status_code == 200
    assert settings.log_level == "WARNING"
    assert response.json()["log_level"] == "WARNING"
    assert applied == ["WARNING"]

    cleared = client.put("/api/v1/settings", json={"log_level": ""})
    assert cleared.status_code == 200
    assert settings.log_level is None
    assert cleared.json()["log_level"] is None
    # Following the environment still reports a concrete effective level.
    assert cleared.json()["effective_log_level"] in {
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    }


def test_v1_settings_put_rejects_an_invalid_log_level(monkeypatch):
    client, settings, _user = _client(monkeypatch=monkeypatch)
    response = client.put("/api/v1/settings", json={"log_level": "TRACE"})
    assert response.status_code == 422
    assert settings.log_level == "ERROR"


def test_v1_settings_put_rejects_invalid_proxy_mode_and_token(monkeypatch):
    client, settings, _user = _client(monkeypatch=monkeypatch)
    bad_mode = client.put("/api/v1/settings", json={"default_proxy_mode": "tor"})
    assert bad_mode.status_code == 422
    assert settings.default_proxy_mode == "panel"

    bad_token = client.put("/api/v1/settings", json={"global_github_token": "not-a-github-token"})
    assert bad_token.status_code == 422
    assert settings.global_github_token == "github_pat_secret123456"


def test_v1_settings_test_email_success_and_failure(monkeypatch):
    client, _settings, _user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.settings.email_service.send_email",
        AsyncMock(return_value=True),
    )
    ok = client.post("/api/v1/settings/test-email", json={"test_email": "ops@example.com"})
    assert ok.status_code == 200
    assert ok.json()["success"] is True
    assert "ops@example.com" in ok.json()["message"]

    monkeypatch.setattr(
        "api.routes.v1.settings.email_service.send_email",
        AsyncMock(return_value=False),
    )
    failed = client.post("/api/v1/settings/test-email", json={"test_email": "ops@example.com"})
    assert failed.status_code == 500


def test_v1_gmail_credentials_and_revoke(monkeypatch):
    client, _settings, _user = _client(monkeypatch=monkeypatch)
    uploaded = {"success": True, "message": "uploaded"}
    revoked = {"success": True, "message": "revoked"}
    monkeypatch.setattr(
        "api.routes.v1.settings.upload_gmail_credentials",
        AsyncMock(return_value=uploaded),
    )
    monkeypatch.setattr(
        "api.routes.v1.settings.revoke_gmail_authorization",
        AsyncMock(return_value=revoked),
    )

    put = client.put(
        "/api/v1/settings/gmail/credentials",
        json={"credentials_json": '{"web": {"client_id": "abc"}}'},
    )
    assert put.status_code == 200
    assert put.json() == uploaded

    delete = client.delete("/api/v1/settings/gmail")
    assert delete.status_code == 200
    assert delete.json() == revoked


def test_v1_gmail_authorize_returns_url(monkeypatch):
    client, _settings, _user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.settings.gmail_oauth_authorize",
        AsyncMock(
            return_value={"authorization_url": "https://accounts.google.com/o", "state": "s1"}
        ),
    )
    response = client.get("/api/v1/settings/gmail/authorize")
    assert response.status_code == 200
    assert response.json()["authorization_url"] == "https://accounts.google.com/o"
    assert response.json()["state"] == "s1"


def test_v1_gmail_credentials_upload_receives_typed_body(monkeypatch):
    client, _settings, _user = _client(monkeypatch=monkeypatch)
    captured: list[GmailCredentialsUploadRequest] = []

    async def capture(request, db, current_user):
        captured.append(request)
        return {"success": True, "message": "ok"}

    monkeypatch.setattr("api.routes.v1.settings.upload_gmail_credentials", capture)
    response = client.put(
        "/api/v1/settings/gmail/credentials",
        json={"credentials_json": '{"installed": {"client_id": "x"}}'},
    )
    assert response.status_code == 200
    assert captured[0].credentials_json == '{"installed": {"client_id": "x"}}'


def test_v1_settings_ai_requires_admin(monkeypatch):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=2, username="member", is_admin=False, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    client = TestClient(app)
    response = client.get("/api/v1/settings/ai")
    assert response.status_code == 403


def test_v1_settings_ai_get_hides_api_key(monkeypatch):
    client, _settings, _user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.settings.legacy_ai.get_system_ai_settings",
        AsyncMock(
            return_value=SimpleNamespace(
                enabled=False,
                base_url="https://api.openai.com/v1",
                model="gpt-4.1",
                api_protocol="chat_completions",
                api_key_configured=True,
                admin_prompt=None,
                private_endpoint_allowlist=[],
                request_timeout_seconds=30,
                history_retention_days=7,
                max_provider_rounds=8,
                max_tool_calls_per_round=20,
                provider_tested=False,
                tool_calling_tested=False,
                streaming_tested=False,
            )
        ),
    )
    response = client.get("/api/v1/settings/ai")
    assert response.status_code == 200
    body = response.json()
    assert body["api_key_configured"] is True
    assert body["model"] == "gpt-4.1"
    assert "api_key" not in body
    assert "sk-" not in response.text


def test_v1_settings_ai_test_accepts_empty_body(monkeypatch):
    client, _settings, _user = _client(monkeypatch=monkeypatch)
    probe = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            text_response_ok=True,
            tool_calling_ok=True,
            streaming_ok=True,
            message="Provider ready",
        )
    )
    monkeypatch.setattr(
        "api.routes.v1.settings.legacy_ai.test_system_ai_settings",
        probe,
    )
    response = client.post("/api/v1/settings/ai/test", json={})
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["message"] == "Provider ready"
    probe.assert_awaited_once()
