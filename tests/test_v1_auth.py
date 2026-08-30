"""Coverage for versioned ``/api/v1/auth`` session, register, and password-reset routes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.application import create_app
from api.password_reset import GENERIC_FORGOT_MESSAGE, RESET_SUCCESS_MESSAGE
from modules import get_current_active_user, get_current_user, get_db, settings


def _database_session(*, user=None):
    return SimpleNamespace(
        add=lambda *_a, **_k: None,
        commit=AsyncMock(),
        refresh=AsyncMock(),
        get=AsyncMock(return_value=user),
    )


def _public_client(monkeypatch, *, db_user=None):
    app = create_app(lifespan=None)

    async def override_db():
        yield _database_session(user=db_user)

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr("api.password_reset.enforce_rate_limit", AsyncMock())
    monkeypatch.setattr("api.password_reset.record_audit_event", AsyncMock())
    return TestClient(app)


def test_v1_auth_me_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_v1_auth_me_returns_session_projection():
    app = create_app(lifespan=None)
    user = SimpleNamespace(
        id=1,
        username="ops",
        email="ops@example.com",
        is_admin=True,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    client = TestClient(app)
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "username": "ops",
        "email": "ops@example.com",
        "is_admin": True,
        "is_active": True,
    }


def test_v1_forgot_password_rejects_invalid_captcha(monkeypatch):
    client = _public_client(monkeypatch)
    monkeypatch.setattr(
        "api.password_reset.captcha_service.validate_captcha",
        AsyncMock(return_value=False),
    )
    response = client.post(
        "/api/v1/auth/forgot-password",
        json={
            "email": "ops@example.com",
            "captcha_token": "tok",
            "captcha_code": "ABCD",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid or expired CAPTCHA code"


def test_v1_forgot_password_hides_unknown_email(monkeypatch):
    client = _public_client(monkeypatch)
    monkeypatch.setattr(
        "api.password_reset.captcha_service.validate_captcha",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr("api.password_reset.User.get_by_email", AsyncMock(return_value=None))
    send = AsyncMock()
    monkeypatch.setattr("api.password_reset.email_service.send_email", send)
    response = client.post(
        "/api/v1/auth/forgot-password",
        json={
            "email": "missing@example.com",
            "captcha_token": "tok",
            "captcha_code": "ABCD",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"success": True, "message": GENERIC_FORGOT_MESSAGE}
    send.assert_not_called()


def test_v1_forgot_password_sends_console_reset_link(monkeypatch):
    client = _public_client(monkeypatch)
    monkeypatch.setattr(settings, "LEGACY_HTML_CONSOLE", "redirect")
    monkeypatch.setattr(settings, "CONSOLE_PUBLIC_URL", "http://console.test")
    monkeypatch.setattr(
        "api.password_reset.captcha_service.validate_captcha",
        AsyncMock(return_value=True),
    )
    user = SimpleNamespace(id=3, username="ops", email="ops@example.com")
    monkeypatch.setattr("api.password_reset.User.get_by_email", AsyncMock(return_value=user))
    monkeypatch.setattr("api.password_reset.generate_api_key", lambda: "reset-token-value")
    monkeypatch.setattr("api.password_reset.PasswordResetToken.create_token", AsyncMock())
    monkeypatch.setattr(
        "api.password_reset.email_service.get_password_reset_template",
        lambda link, name: (f"html:{link}", f"text:{link}"),
    )
    send = AsyncMock(return_value=True)
    monkeypatch.setattr("api.password_reset.email_service.send_email", send)

    response = client.post(
        "/api/v1/auth/forgot-password",
        json={
            "email": "ops@example.com",
            "captcha_token": "tok",
            "captcha_code": "ABCD",
        },
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    send.assert_awaited_once()
    assert send.await_args.args[2] == "Password Reset Request - CS2 Server Manager"
    assert (
        send.await_args.args[3] == "html:http://console.test/reset-password?token=reset-token-value"
    )


def test_v1_reset_password_rejects_invalid_token(monkeypatch):
    client = _public_client(monkeypatch)
    monkeypatch.setattr(
        "api.password_reset.PasswordResetToken.get_by_token",
        AsyncMock(return_value=None),
    )
    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": "nope", "new_password": "newpass"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid or expired reset token"


def test_v1_reset_password_updates_hash(monkeypatch):
    user = SimpleNamespace(id=1, hashed_password="old")
    client = _public_client(monkeypatch, db_user=user)
    token = SimpleNamespace(user_id=1, is_valid=True, used=False)
    monkeypatch.setattr(
        "api.password_reset.PasswordResetToken.get_by_token",
        AsyncMock(return_value=token),
    )
    monkeypatch.setattr(
        "api.password_reset.get_password_hash_async",
        AsyncMock(return_value="hashed-new"),
    )
    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": "good-token", "new_password": "newpass"},
    )
    assert response.status_code == 200
    assert response.json() == {"success": True, "message": RESET_SUCCESS_MESSAGE}
    assert user.hashed_password == "hashed-new"
    assert token.used is True


def _register_payload(**overrides):
    body = {
        "username": "newbie",
        "email": "newbie@example.com",
        "password": "secret1",
        "captcha_token": "tok",
        "captcha_code": "ABCD",
    }
    body.update(overrides)
    return body


def test_v1_register_rejects_invalid_captcha(monkeypatch):
    client = _public_client(monkeypatch)
    monkeypatch.setattr("api.registration.enforce_rate_limit", AsyncMock())
    monkeypatch.setattr(
        "api.registration.captcha_service.validate_captcha",
        AsyncMock(return_value=False),
    )
    response = client.post("/api/v1/auth/register", json=_register_payload())
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid or expired CAPTCHA code"


def test_v1_register_rejects_duplicate_username(monkeypatch):
    client = _public_client(monkeypatch)
    monkeypatch.setattr("api.registration.enforce_rate_limit", AsyncMock())
    monkeypatch.setattr(
        "api.registration.captcha_service.validate_captcha",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "api.registration.User.get_by_username",
        AsyncMock(return_value=SimpleNamespace(id=2)),
    )
    response = client.post("/api/v1/auth/register", json=_register_payload())
    assert response.status_code == 400
    assert response.json()["detail"] == "Username already registered"


def test_v1_register_rejects_duplicate_email(monkeypatch):
    client = _public_client(monkeypatch)
    monkeypatch.setattr("api.registration.enforce_rate_limit", AsyncMock())
    monkeypatch.setattr(
        "api.registration.captcha_service.validate_captcha",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr("api.registration.User.get_by_username", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "api.registration.User.get_by_email",
        AsyncMock(return_value=SimpleNamespace(id=3)),
    )
    response = client.post("/api/v1/auth/register", json=_register_payload())
    assert response.status_code == 400
    assert response.json()["detail"] == "Email already registered"


def test_v1_register_creates_member(monkeypatch):
    created = {}

    def add(user):
        created["user"] = user

    async def refresh(user):
        user.id = 12

    db = SimpleNamespace(add=add, commit=AsyncMock(), refresh=refresh, get=AsyncMock())
    app = create_app(lifespan=None)

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr("api.registration.enforce_rate_limit", AsyncMock())
    monkeypatch.setattr("api.registration.record_audit_event", AsyncMock())
    monkeypatch.setattr(
        "api.registration.captcha_service.validate_captcha",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr("api.registration.User.get_by_username", AsyncMock(return_value=None))
    monkeypatch.setattr("api.registration.User.get_by_email", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "api.registration.get_password_hash_async",
        AsyncMock(return_value="hashed-pass"),
    )
    client = TestClient(app)
    response = client.post("/api/v1/auth/register", json=_register_payload())
    assert response.status_code == 201
    assert response.json() == {
        "id": 12,
        "username": "newbie",
        "email": "newbie@example.com",
        "is_admin": False,
        "is_active": True,
    }
    assert created["user"].hashed_password == "hashed-pass"
    assert created["user"].is_admin is False


def test_v1_google_config_is_public_and_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", None)
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/auth/google-config")
    assert response.status_code == 200
    assert response.json() == {"client_id": "", "enabled": False}


def test_v1_google_config_exposes_configured_client(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/auth/google-config")
    assert response.status_code == 200
    assert response.json() == {
        "client_id": "test-client.apps.googleusercontent.com",
        "enabled": True,
    }


def test_v1_google_oauth_requires_configuration(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr("api.routes.auth.enforce_rate_limit", AsyncMock())
    monkeypatch.setattr("api.routes.auth.record_audit_event", AsyncMock())
    client = TestClient(create_app(lifespan=None))
    response = client.post("/api/v1/auth/google-oauth", json={"id_token": "not-a-token"})
    assert response.status_code == 500
    assert "not configured" in response.json()["detail"]


def test_v1_google_oauth_logs_in_existing_google_user(monkeypatch):
    user = SimpleNamespace(id=7, username="ops", is_active=True)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr("api.routes.auth.enforce_rate_limit", AsyncMock())
    monkeypatch.setattr("api.routes.auth.record_audit_event", AsyncMock())
    monkeypatch.setattr(
        "api.routes.auth.to_thread.run_sync",
        AsyncMock(return_value={"sub": "google-sub-7", "email": "ops@example.com"}),
    )
    monkeypatch.setattr("api.routes.auth.User.get_by_google_id", AsyncMock(return_value=user))
    monkeypatch.setattr("api.routes.auth.create_access_token", lambda **_k: "jwt-for-ops")
    monkeypatch.setattr("api.routes.auth.set_web_session_cookie", lambda *_a, **_k: None)

    app = create_app(lifespan=None)

    async def override_db():
        yield _database_session(user=user)

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    response = client.post("/api/v1/auth/google-oauth", json={"id_token": "valid-id-token"})
    assert response.status_code == 200
    assert response.json() == {"access_token": "jwt-for-ops", "token_type": "bearer"}
