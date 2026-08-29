"""Coverage for the versioned ``/api/v1/profile`` personal-center contract."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db


def _database_session():
    result = SimpleNamespace(scalar_one_or_none=lambda: None)
    return SimpleNamespace(
        add=lambda *_a, **_k: None,
        commit=AsyncMock(),
        refresh=AsyncMock(),
        execute=AsyncMock(return_value=result),
    )


async def _fake_db():
    yield _database_session()


def _client(*, retries: int = 20, monkeypatch=None, **extra):
    app = create_app(lifespan=None)
    values = {
        "id": 1,
        "username": "ops",
        "email": "ops@example.com",
        "is_admin": False,
        "is_active": True,
        "hashed_password": "hashed",
        "steamcmd_max_retries": retries,
        "steam_api_key": None,
        "github_token": None,
        "api_key": None,
        "created_at": None,
        "updated_at": None,
        "s3_enabled": False,
        "s3_endpoint_url": None,
        "s3_region": None,
        "s3_bucket": None,
        "s3_access_key_id": None,
        "s3_secret_access_key": None,
        "s3_prefix": None,
        "s3_use_ssl": True,
        "s3_retention_count": 10,
    }
    values.update(extra)
    user = SimpleNamespace(**values)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    if monkeypatch is not None:
        monkeypatch.setattr("api.routes.v1.profile.record_audit_event", AsyncMock())
        monkeypatch.setattr(
            "api.routes.v1.profile.captcha_service.validate_captcha",
            AsyncMock(return_value=True),
        )
    return TestClient(app), user


def test_v1_profile_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/profile")
    assert response.status_code == 401


def test_v1_profile_returns_default_steamcmd_retry_budget():
    client, _user = _client()
    response = client.get("/api/v1/profile")
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "ops"
    assert body["steamcmd_max_retries"] == 20
    assert body["steamcmd_max_retries_default"] == 20
    assert body["steamcmd_max_retries_limit"] == 100
    assert body["has_steam_api_key"] is False
    assert body["has_github_token"] is False
    assert body["has_api_key"] is False
    assert "hashed_password" not in body
    assert "steam_api_key" not in body
    assert "github_token" not in body
    assert "api_key" not in body


def test_v1_profile_exposes_prefixes_not_secrets():
    client, _user = _client(
        steam_api_key="A" * 32,
        github_token="github_pat_abcdefghijk",
        api_key="k" * 64,
    )
    response = client.get("/api/v1/profile")
    body = response.json()
    assert body["has_steam_api_key"] is True
    assert body["steam_api_key_prefix"] == "AAAAAAAA..."
    assert body["has_github_token"] is True
    assert body["github_token_prefix"] == "github_pat_a..."
    assert body["has_api_key"] is True
    assert "A" * 32 not in response.text
    assert "github_pat_abcdefghijk" not in response.text
    assert "k" * 64 not in response.text


def test_v1_profile_patch_persists_retry_budget():
    client, user = _client()
    response = client.patch("/api/v1/profile", json={"steamcmd_max_retries": 12})
    assert response.status_code == 200
    assert user.steamcmd_max_retries == 12
    assert response.json()["steamcmd_max_retries"] == 12


def test_v1_profile_patch_rejects_out_of_range_retries():
    client, _user = _client()
    response = client.patch("/api/v1/profile", json={"steamcmd_max_retries": 101})
    assert response.status_code == 422


def test_v1_profile_patch_requires_captcha_for_email(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.profile.captcha_service.validate_captcha",
        AsyncMock(return_value=False),
    )
    response = client.patch(
        "/api/v1/profile",
        json={
            "email": "new@example.com",
            "captcha_token": "tok",
            "captcha_code": "ABCD",
        },
    )
    assert response.status_code == 400


def test_v1_profile_patch_updates_email_and_keys(monkeypatch):
    client, user = _client(monkeypatch=monkeypatch)
    response = client.patch(
        "/api/v1/profile",
        json={
            "email": "new@example.com",
            "steam_api_key": "B" * 32,
            "github_token": "github_pat_newtokenvalue",
            "captcha_token": "tok",
            "captcha_code": "ABCD",
        },
    )
    assert response.status_code == 200
    assert user.email == "new@example.com"
    assert user.steam_api_key == "B" * 32
    assert user.github_token == "github_pat_newtokenvalue"
    body = response.json()
    assert body["has_steam_api_key"] is True
    assert "steam_api_key" not in body
    assert "github_token" not in body


def test_v1_profile_patch_clears_keys(monkeypatch):
    client, user = _client(
        monkeypatch=monkeypatch,
        steam_api_key="A" * 32,
        github_token="github_pat_abcdefghijk",
    )
    response = client.patch(
        "/api/v1/profile",
        json={
            "clear_steam_api_key": True,
            "clear_github_token": True,
            "captcha_token": "tok",
            "captcha_code": "ABCD",
        },
    )
    assert response.status_code == 200
    assert user.steam_api_key is None
    assert user.github_token is None
    assert response.json()["has_steam_api_key"] is False


def test_v1_profile_password_change(monkeypatch):
    client, user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.profile.verify_password_async",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "api.routes.v1.profile.get_password_hash_async",
        AsyncMock(return_value="new-hash"),
    )
    response = client.post(
        "/api/v1/profile/password",
        json={
            "current_password": "oldpass",
            "new_password": "newpass",
            "confirm_password": "newpass",
            "captcha_token": "tok",
            "captcha_code": "ABCD",
        },
    )
    assert response.status_code == 200
    assert user.hashed_password == "new-hash"
    assert response.json()["success"] is True


def test_v1_profile_password_rejects_mismatch(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.profile.verify_password_async",
        AsyncMock(return_value=True),
    )
    response = client.post(
        "/api/v1/profile/password",
        json={
            "current_password": "oldpass",
            "new_password": "newpass",
            "confirm_password": "otherpw",
            "captcha_token": "tok",
            "captcha_code": "ABCD",
        },
    )
    assert response.status_code == 400


def test_v1_profile_api_key_generate_and_revoke(monkeypatch):
    client, user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.profile.User.get_by_api_key",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.profile.generate_api_key",
        lambda: "z" * 64,
    )
    created = client.post(
        "/api/v1/profile/api-key",
        json={"captcha_token": "tok", "captcha_code": "ABCD"},
    )
    assert created.status_code == 200
    assert created.json()["api_key"] == "z" * 64
    assert user.api_key == "z" * 64

    revealed = client.get("/api/v1/profile/api-key")
    assert revealed.status_code == 200
    assert revealed.json()["api_key"] == "z" * 64

    revoked = client.delete("/api/v1/profile/api-key")
    assert revoked.status_code == 200
    assert user.api_key is None


def test_v1_profile_api_key_missing():
    client, _user = _client()
    response = client.get("/api/v1/profile/api-key")
    assert response.status_code == 404


def test_v1_profile_s3_round_trip(monkeypatch):
    client, user = _client(monkeypatch=monkeypatch)
    empty = client.get("/api/v1/profile/s3")
    assert empty.status_code == 200
    assert empty.json()["is_configured"] is False
    assert "s3_secret_access_key" not in empty.json()

    saved = client.put(
        "/api/v1/profile/s3",
        json={
            "enabled": True,
            "endpoint_url": "https://example.r2.cloudflarestorage.com",
            "region": "auto",
            "bucket": "backups",
            "access_key_id": "AKIAEXAMPLE",
            "secret_access_key": "super-secret",
            "prefix": "cs2-backups",
            "use_ssl": True,
            "retention_count": 8,
            "captcha_token": "tok",
            "captcha_code": "ABCD",
        },
    )
    assert saved.status_code == 200
    body = saved.json()
    assert user.s3_enabled is True
    assert user.s3_secret_access_key == "super-secret"
    assert body["has_secret"] is True
    assert body["bucket"] == "backups"
    assert "secret_access_key" not in body
    assert "super-secret" not in saved.text


def test_v1_profile_s3_test(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.profile.s3_backup_service.test_connection",
        AsyncMock(
            return_value=(
                True,
                "ok",
                [{"name": "list", "status": "success", "message": "listed"}],
            )
        ),
    )
    response = client.post("/api/v1/profile/s3/test")
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["steps"][0]["name"] == "list"


def test_v1_profile_gslt_requires_steam_api_key(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    response = client.post(
        "/api/v1/profile/gslt",
        json={
            "server_name": "lan-ops",
            "captcha_token": "tok",
            "captcha_code": "ABCD",
        },
    )
    assert response.status_code == 400
    assert "Steam API key not set" in response.json()["detail"]


def test_v1_profile_gslt_requires_captcha(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch, steam_api_key="A" * 32)
    monkeypatch.setattr(
        "api.routes.v1.profile.captcha_service.validate_captcha",
        AsyncMock(return_value=False),
    )
    response = client.post(
        "/api/v1/profile/gslt",
        json={
            "server_name": "lan-ops",
            "captcha_token": "tok",
            "captcha_code": "ABCD",
        },
    )
    assert response.status_code == 400


def test_v1_profile_gslt_returns_generated_token(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch, steam_api_key="A" * 32)
    monkeypatch.setattr(
        "api.routes.v1.profile.steam_api_service.create_game_server_account",
        AsyncMock(
            return_value=(
                True,
                {"success": True, "login_token": "GSLTTOKEN123", "steamid": "7656119"},
            )
        ),
    )
    response = client.post(
        "/api/v1/profile/gslt",
        json={
            "server_name": "lan-ops",
            "captcha_token": "tok",
            "captcha_code": "ABCD",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["login_token"] == "GSLTTOKEN123"
    assert body["steamid"] == "7656119"


def test_v1_profile_gslt_surfaces_steam_error(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch, steam_api_key="A" * 32)
    monkeypatch.setattr(
        "api.routes.v1.profile.steam_api_service.create_game_server_account",
        AsyncMock(return_value=(False, {"success": False, "error": "Steam said no"})),
    )
    response = client.post(
        "/api/v1/profile/gslt",
        json={
            "captcha_token": "tok",
            "captcha_code": "ABCD",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Steam said no"


def test_v1_profile_ai_hides_key(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.profile.legacy_ai.get_user_ai_settings",
        AsyncMock(
            return_value=SimpleNamespace(
                mode="custom",
                base_url="https://api.example.com/v1",
                model="gpt-test",
                api_protocol="chat_completions",
                api_key_configured=True,
                reasoning_effort="high",
                temperature=None,
                top_p=None,
                max_completion_tokens=2048,
                token_limit_parameter="max_completion_tokens",
                frequency_penalty=None,
                presence_penalty=None,
                verbosity=None,
                parallel_tool_calls=None,
                provider_tested=True,
                tool_calling_tested=False,
                streaming_tested=False,
                effective_enabled=True,
                effective_source="custom",
            )
        ),
    )
    response = client.get("/api/v1/profile/ai")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "custom"
    assert body["api_key_configured"] is True
    assert body["reasoning_effort"] == "high"
    assert "api_key" not in body
