"""HTTP coverage for versioned passkey registration, login, and management."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db
from services.passkeys.types import PasskeyError


def _db():
    return SimpleNamespace(
        add=lambda *_a, **_k: None,
        commit=AsyncMock(),
        refresh=AsyncMock(),
        get=AsyncMock(return_value=None),
        execute=AsyncMock(),
        delete=AsyncMock(),
    )


def _authed_client(monkeypatch, *, user):
    app = create_app(lifespan=None)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    async def override_db():
        yield _db()

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr("api.routes.v1.passkeys.enforce_rate_limit", AsyncMock())
    monkeypatch.setattr("api.routes.v1.passkeys.record_audit_event", AsyncMock())
    return TestClient(app)


def _public_client(monkeypatch):
    app = create_app(lifespan=None)

    async def override_db():
        yield _db()

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr("api.routes.v1.passkeys.enforce_rate_limit", AsyncMock())
    monkeypatch.setattr("api.routes.v1.passkeys.record_audit_event", AsyncMock())
    return TestClient(app)


def _user():
    return SimpleNamespace(
        id=7, username="ops", email="ops@example.com", is_admin=True, is_active=True
    )


def test_list_passkeys_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    assert client.get("/api/v1/auth/passkeys").status_code == 401


def test_list_passkeys_returns_public_projection(monkeypatch):
    user = _user()
    client = _authed_client(monkeypatch, user=user)
    monkeypatch.setattr(
        "api.routes.v1.passkeys.list_passkeys",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    id=3,
                    nickname="YubiKey",
                    transports=["usb", "nfc"],
                    created_at=None,
                    last_used_at=None,
                    backup_eligible=False,
                    backup_state=False,
                )
            ]
        ),
    )
    response = client.get("/api/v1/auth/passkeys")
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": 3,
                "nickname": "YubiKey",
                "transports": ["usb", "nfc"],
                "created_at": None,
                "last_used_at": None,
                "backup_eligible": False,
                "backup_state": False,
            }
        ]
    }


def test_login_options_rejects_ip_origin(monkeypatch):
    client = _public_client(monkeypatch)
    response = client.post(
        "/api/v1/auth/passkeys/login/options",
        json={},
        headers={"origin": "http://192.168.1.8:31800"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "passkey_ip_origin"


def test_login_options_returns_public_key_json(monkeypatch):
    client = _public_client(monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.passkeys.start_authentication",
        AsyncMock(return_value={"challenge": "abc", "rpId": "localhost"}),
    )
    response = client.post(
        "/api/v1/auth/passkeys/login/options",
        json={"username": "ops"},
        headers={"origin": "http://localhost:31800"},
    )
    assert response.status_code == 200
    assert response.json()["public_key"]["challenge"] == "abc"


def test_login_verify_sets_session_cookie(monkeypatch):
    client = _public_client(monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.passkeys.complete_authentication",
        AsyncMock(return_value=_user()),
    )
    response = client.post(
        "/api/v1/auth/passkeys/login/verify",
        json={"credential": {"id": "cred", "type": "public-key", "response": {}}},
        headers={"origin": "http://localhost:31800"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert "upkk_access_token" in response.cookies


def test_login_verify_maps_passkey_error(monkeypatch):
    client = _public_client(monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.passkeys.complete_authentication",
        AsyncMock(side_effect=PasskeyError("passkey_verification_failed", 401)),
    )
    response = client.post(
        "/api/v1/auth/passkeys/login/verify",
        json={"credential": {"id": "cred", "response": {}}},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "passkey_verification_failed"


def test_register_and_delete_passkey(monkeypatch):
    user = _user()
    client = _authed_client(monkeypatch, user=user)
    monkeypatch.setattr(
        "api.routes.v1.passkeys.start_registration",
        AsyncMock(return_value={"challenge": "reg"}),
    )
    options = client.post(
        "/api/v1/auth/passkeys/register/options", headers={"origin": "http://localhost"}
    )
    assert options.status_code == 200
    assert options.json()["public_key"]["challenge"] == "reg"

    created = SimpleNamespace(
        id=11,
        nickname="Laptop",
        transports=["internal"],
        created_at=None,
        last_used_at=None,
        backup_eligible=True,
        backup_state=True,
    )
    monkeypatch.setattr(
        "api.routes.v1.passkeys.complete_registration", AsyncMock(return_value=created)
    )
    verify = client.post(
        "/api/v1/auth/passkeys/register/verify",
        json={"credential": {"id": "cred", "response": {}}, "nickname": "Laptop"},
        headers={"origin": "http://localhost"},
    )
    assert verify.status_code == 200
    assert verify.json()["id"] == 11
    assert verify.json()["nickname"] == "Laptop"

    monkeypatch.setattr("api.routes.v1.passkeys.delete_credential", AsyncMock())
    deleted = client.delete("/api/v1/auth/passkeys/11")
    assert deleted.status_code == 200
    assert deleted.json()["success"] is True

    monkeypatch.setattr(
        "api.routes.v1.passkeys.rename_credential",
        AsyncMock(return_value=created),
    )
    patched = client.patch("/api/v1/auth/passkeys/11", json={"nickname": "Laptop"})
    assert patched.status_code == 200
    assert patched.json()["nickname"] == "Laptop"


def test_delete_missing_passkey_returns_404(monkeypatch):
    client = _authed_client(monkeypatch, user=_user())
    monkeypatch.setattr(
        "api.routes.v1.passkeys.delete_credential",
        AsyncMock(side_effect=PasskeyError("passkey_not_found", 404)),
    )
    response = client.delete("/api/v1/auth/passkeys/99")
    assert response.status_code == 404
    assert response.json()["detail"] == "passkey_not_found"
