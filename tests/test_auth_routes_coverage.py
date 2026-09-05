"""行为测试：认证路由的成功、失败和敏感配置分支。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from api.routes import auth
from modules import (
    ApiKeyGenerate,
    ForgotPasswordRequest,
    GenerateServerTokenRequest,
    GoogleOAuthRequest,
    PasswordReset,
    ResetPasswordRequest,
    S3SettingsUpdate,
    UserLogin,
    UserProfileUpdate,
)


class _DB:
    def __init__(self, result=None):
        self.result = result
        self.commits = 0
        self.refreshed = []

    async def execute(self, _statement):
        return self.result

    async def commit(self):
        self.commits += 1

    async def refresh(self, item):
        self.refreshed.append(item)

    def add(self, _item):
        return None


class _Scalar:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


def _request(headers=None, cookies=None):
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth",
        "headers": [
            (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
        ],
        "query_string": b"",
        "client": ("test", 1),
        "server": ("test", 80),
        "scheme": "http",
    }
    request = Request(scope)
    if cookies:
        request._cookies = cookies
    return request


def _user(**values):
    defaults = {
        "id": 8,
        "username": "alice",
        "email": "alice@example.com",
        "hashed_password": "hash",
        "is_active": True,
        "api_key": None,
        "updated_at": None,
        "steam_api_key": None,
        "github_token": None,
        "has_github_token": False,
        "s3_enabled": False,
        "s3_endpoint_url": None,
        "s3_region": None,
        "s3_bucket": None,
        "s3_access_key_id": None,
        "s3_prefix": None,
        "s3_use_ssl": True,
        "s3_retention_count": 3,
        "s3_secret_access_key": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_public_register_login_and_session_branches(monkeypatch):
    request = _request()
    user = _user()
    monkeypatch.setattr(auth, "register_user", AsyncMock(return_value="registered"))
    data = SimpleNamespace(
        username="alice", email="a@e", password="pw", captcha_token="t", captcha_code="AB12"
    )
    assert await auth.register(data, request, _DB()) == "registered"

    monkeypatch.setattr(auth, "enforce_rate_limit", AsyncMock())
    monkeypatch.setattr(auth, "require_captcha", AsyncMock())
    monkeypatch.setattr(auth, "record_audit_event", AsyncMock())
    monkeypatch.setattr(auth.User, "get_by_username", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await auth.login(UserLogin(username="alice", password="wrong"), request, Response(), _DB())
    assert exc.value.status_code == 401

    monkeypatch.setattr(auth.User, "get_by_username", AsyncMock(return_value=user))
    monkeypatch.setattr(auth, "verify_password_async", AsyncMock(return_value=False))
    with pytest.raises(HTTPException):
        await auth.login(UserLogin(username="alice", password="wrong"), request, Response(), _DB())
    monkeypatch.setattr(auth, "verify_password_async", AsyncMock(return_value=True))
    user.is_active = False
    with pytest.raises(HTTPException) as exc:
        await auth.login(UserLogin(username="alice", password="pw"), request, Response(), _DB())
    assert exc.value.status_code == 400

    user.is_active = True
    monkeypatch.setattr(auth, "create_access_token", lambda **_kwargs: "access")
    monkeypatch.setattr(auth, "set_web_session_cookie", lambda *_args: None)
    result = await auth.login(
        UserLogin(username="alice", password="pw"), request, Response(), _DB()
    )
    assert result == {"access_token": "access", "token_type": "bearer"}
    assert await auth.get_google_config() == {
        "client_id": auth.settings.GOOGLE_CLIENT_ID,
        "enabled": bool(auth.settings.GOOGLE_CLIENT_ID),
    }
    assert await auth.get_current_user_info(user) is user

    with pytest.raises(HTTPException):
        await auth.bootstrap_web_session(_request(), Response(), user)
    assert await auth.bootstrap_web_session(
        _request({"authorization": "Bearer browser-token"}), Response(), user
    ) == {"success": True}


@pytest.mark.asyncio
async def test_password_profile_api_key_and_s3_routes(monkeypatch):
    user = _user(
        api_key="old", steam_api_key="steam", github_token="gh-token", has_github_token=True
    )
    request = _request()
    monkeypatch.setattr(auth, "require_captcha", AsyncMock())
    monkeypatch.setattr(auth, "record_audit_event", AsyncMock())
    monkeypatch.setattr(auth, "verify_password_async", AsyncMock(return_value=False))
    with pytest.raises(HTTPException):
        await auth.reset_password(
            PasswordReset(
                current_password="badpass", new_password="newpass", confirm_password="newpass"
            ),
            request,
            user,
            _DB(),
        )
    monkeypatch.setattr(auth, "verify_password_async", AsyncMock(return_value=True))
    with pytest.raises(HTTPException):
        await auth.reset_password(
            PasswordReset(
                current_password="oldpass", new_password="newpass", confirm_password="otherpass"
            ),
            request,
            user,
            _DB(),
        )
    monkeypatch.setattr(auth, "get_password_hash_async", AsyncMock(return_value="new-hash"))
    assert (
        await auth.reset_password(
            PasswordReset(
                current_password="oldpass", new_password="newpass", confirm_password="newpass"
            ),
            request,
            user,
            _DB(),
        )
    )["success"] is True

    monkeypatch.setattr(auth, "require_captcha", AsyncMock())
    duplicate = _user(id=9)
    with pytest.raises(HTTPException):
        await auth.update_profile(
            UserProfileUpdate(email="taken@example.com"), request, user, _DB(_Scalar(duplicate))
        )
    empty = UserProfileUpdate(email="new@example.com", steam_api_key="  ", github_token="  ")
    assert await auth.update_profile(empty, request, user, _DB(_Scalar(None))) is user
    assert user.steam_api_key is None and user.github_token is None

    user.api_key = None
    with pytest.raises(HTTPException):
        await auth.get_api_key(user)
    user.api_key = "key"
    assert (await auth.get_api_key(user))["api_key"] == "key"
    monkeypatch.setattr(auth, "generate_api_key", lambda: "generated")
    monkeypatch.setattr(auth.User, "get_by_api_key", AsyncMock(return_value=None))
    assert (await auth.generate_user_api_key(ApiKeyGenerate(), request, user, _DB()))[
        "api_key"
    ] == "generated"
    assert (await auth.revoke_api_key(request, user, _DB()))["success"] is True
    with pytest.raises(HTTPException):
        await auth.revoke_api_key(request, _user(), _DB())
    user.steam_api_key = "steam"
    assert (await auth.get_steam_api_key(user))["steam_api_key"] == "steam"
    user.github_token = "gh-token"
    user.has_github_token = True
    assert (await auth.get_github_token_status(user))["token_prefix"] == "gh-token..."

    monkeypatch.setattr(auth.s3_backup_service, "get_retention_count", lambda _u: 4)
    monkeypatch.setattr(auth.s3_backup_service, "is_configured", lambda _u: True)
    assert (await auth.get_s3_settings(user)).retention_count == 4
    data = S3SettingsUpdate(
        enabled=True,
        endpoint_url="",
        region="us",
        bucket="bucket",
        access_key_id="ak",
        prefix="p",
        use_ssl=False,
        retention_count=5,
        clear_secret=True,
        secret_access_key="ignored",
    )
    result = await auth.update_s3_settings(data, request, user, _DB())
    assert result.enabled is True and user.s3_secret_access_key is None
    monkeypatch.setattr(
        auth.s3_backup_service, "test_connection", AsyncMock(return_value=(True, "ok", []))
    )
    assert (await auth.test_s3_settings(user))["success"] is True


@pytest.mark.asyncio
async def test_server_token_password_reset_wrappers_and_logout(monkeypatch):
    user = _user(username="bob", steam_api_key=None)
    monkeypatch.setattr(auth, "require_captcha", AsyncMock())
    with pytest.raises(HTTPException):
        await auth.generate_server_token(GenerateServerTokenRequest(), user, _DB())
    user.steam_api_key = "steam-key"
    monkeypatch.setattr(
        auth.steam_api_service,
        "create_game_server_account",
        AsyncMock(return_value=(False, {"error": "bad"})),
    )
    result = await auth.generate_server_token(
        GenerateServerTokenRequest(server_name="  "), user, _DB()
    )
    assert result.success is False
    monkeypatch.setattr(
        auth.steam_api_service,
        "create_game_server_account",
        AsyncMock(return_value=(True, {"success": True, "login_token": "GSLT"})),
    )
    result = await auth.generate_server_token(
        GenerateServerTokenRequest(server_name="  server  "), user, _DB()
    )
    assert result.login_token == "GSLT"

    monkeypatch.setattr(
        "api.password_reset.request_password_reset", AsyncMock(return_value={"ok": True})
    )
    assert await auth.forgot_password(
        ForgotPasswordRequest(email="a@example.com"), _request(), _DB()
    ) == {"ok": True}
    monkeypatch.setattr(
        "api.password_reset.complete_password_reset", AsyncMock(return_value={"ok": True})
    )
    assert await auth.reset_password_with_token(
        ResetPasswordRequest(token="x", new_password="newpass"), _request(), _DB()
    ) == {"ok": True}

    monkeypatch.setattr("modules.auth._get_active_user_for_token", AsyncMock(return_value=user))
    monkeypatch.setattr("modules.auth.web_session_cookie_name", lambda: "session")
    monkeypatch.setattr(auth, "record_audit_event", AsyncMock())
    request = _request(cookies={"session": "token"})
    monkeypatch.setattr(auth, "clear_web_session_cookie", lambda _response: None)
    assert (await auth.logout(request, Response(), _DB()))["success"] is True


@pytest.mark.asyncio
async def test_google_oauth_configuration_invalid_existing_and_registration(monkeypatch):
    user = _user(google_id="google-id")
    request = _request()
    response = Response()
    monkeypatch.setattr(auth, "enforce_rate_limit", AsyncMock())
    monkeypatch.setattr(auth, "record_audit_event", AsyncMock())
    monkeypatch.setattr(auth, "set_web_session_cookie", lambda *_args: None)
    settings = SimpleNamespace(GOOGLE_CLIENT_ID="client", JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30)
    monkeypatch.setattr(auth, "settings", settings)
    oauth = GoogleOAuthRequest(id_token="id", username="new", password="secret1")
    monkeypatch.setattr(auth.to_thread, "run_sync", AsyncMock(side_effect=ValueError("invalid")))
    with pytest.raises(HTTPException) as exc:
        await auth.google_oauth_login(oauth, request, response, _DB())
    assert exc.value.status_code == 401

    monkeypatch.setattr(
        auth.to_thread, "run_sync", AsyncMock(return_value={"sub": "google-id", "email": "a@e"})
    )
    monkeypatch.setattr(auth.User, "get_by_google_id", AsyncMock(return_value=user))
    monkeypatch.setattr(auth, "create_access_token", lambda **_kwargs: "token")
    assert (await auth.google_oauth_login(oauth, request, response, _DB()))[
        "access_token"
    ] == "token"
    user.is_active = False
    with pytest.raises(HTTPException):
        await auth.google_oauth_login(oauth, request, response, _DB())

    monkeypatch.setattr(auth.User, "get_by_google_id", AsyncMock(return_value=None))
    with pytest.raises(HTTPException):
        await auth.google_oauth_login(GoogleOAuthRequest(id_token="id"), request, response, _DB())
    user.is_active = True
    monkeypatch.setattr(auth.User, "get_by_username", AsyncMock(return_value=None))
    monkeypatch.setattr(auth.User, "get_by_email", AsyncMock(return_value=None))
    monkeypatch.setattr(auth, "get_password_hash_async", AsyncMock(return_value="hash"))
    db = _DB()
    result = await auth.google_oauth_login(oauth, request, response, db)
    assert result["access_token"] == "token"
    monkeypatch.setattr(auth.User, "get_by_username", AsyncMock(return_value=SimpleNamespace()))
    with pytest.raises(HTTPException):
        await auth.google_oauth_login(oauth, request, response, _DB())
