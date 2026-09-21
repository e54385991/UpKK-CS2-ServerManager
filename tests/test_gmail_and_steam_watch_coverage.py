"""覆盖 Gmail 认证边界和 SteamCMD 断线观察器的本地状态路径。"""

from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes import gmail_oauth as gmail
from services import steamcmd_watch as watch


class _Db:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


def _user():
    return SimpleNamespace(id=1)


@pytest.mark.asyncio
async def test_gmail_upload_revoke_status_and_authorize_guards(monkeypatch):
    db = _Db()
    current = _user()
    settings = SimpleNamespace(gmail_credentials_json=None, gmail_token_json=None)
    get_settings = AsyncMock(return_value=settings)
    monkeypatch.setattr(gmail.SystemSettings, "get_or_create_settings", get_settings)

    with pytest.raises(HTTPException) as exc_info:
        await gmail.upload_gmail_credentials(SimpleNamespace(credentials_json="{"), db, current)
    assert exc_info.value.status_code == 400
    with pytest.raises(HTTPException) as exc_info:
        await gmail.upload_gmail_credentials(SimpleNamespace(credentials_json="{}"), db, current)
    assert exc_info.value.status_code == 500
    request = SimpleNamespace(credentials_json='{"web": {"client_id": "id"}}')
    assert (await gmail.upload_gmail_credentials(request, db, current))["success"] is True
    assert settings.gmail_credentials_json == request.credentials_json
    settings.gmail_token_json = "token"
    assert await gmail.gmail_oauth_status(db, current) == {
        "credentials_configured": True,
        "token_configured": True,
        "ready": True,
    }
    assert (await gmail.revoke_gmail_authorization(db, current))["success"] is True
    assert settings.gmail_token_json is None

    settings.gmail_credentials_json = None
    with pytest.raises(HTTPException) as exc_info:
        await gmail.gmail_oauth_authorize(SimpleNamespace(), db, current)
    assert exc_info.value.status_code == 500
    denied = await gmail.gmail_oauth_callback(SimpleNamespace(), db=db, error="denied")
    assert denied.status_code == 302
    response = await gmail.gmail_oauth_callback(SimpleNamespace(), db=db)
    assert response.status_code == 302
    assert response.headers["location"] == "/settings?gmail_auth=error"


@pytest.mark.asyncio
async def test_gmail_oauth_flow_success_and_failure_redirect(monkeypatch):
    class _Credentials:
        token = "access"
        refresh_token = "refresh"
        token_uri = "https://oauth.invalid/token"
        client_id = "client"
        client_secret = "secret"
        scopes = ["gmail.send"]

    class _Flow:
        credentials = _Credentials()
        redirect_uris: list[str] = []
        verifiers: list[str | None] = []
        fetch_count = 0

        def __init__(self, code_verifier: str | None = None):
            self.code_verifier = code_verifier

        @classmethod
        def from_client_config(cls, *_args, **kwargs):
            verifier = kwargs.get("code_verifier")
            cls.redirect_uris.append(kwargs.get("redirect_uri", ""))
            cls.verifiers.append(verifier)
            return cls(code_verifier=verifier)

        def authorization_url(self, **kwargs):
            return "https://accounts.invalid/auth", kwargs.get("state", "state")

        def fetch_token(self, **_kwargs):
            type(self).fetch_count += 1
            return None

    flow_module = ModuleType("google_auth_oauthlib.flow")
    flow_module.Flow = _Flow
    package = ModuleType("google_auth_oauthlib")
    package.flow = flow_module
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", package)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_module)
    monkeypatch.setattr(gmail.settings, "BACKEND_URL", "https://panel.invalid")
    monkeypatch.setattr(gmail.settings, "SECRET_KEY", "s" * 32)
    _Flow.redirect_uris = []
    _Flow.verifiers = []
    _Flow.fetch_count = 0
    db = _Db()
    settings = SimpleNamespace(
        gmail_credentials_json='{"web": {"client_id": "id"}}', gmail_token_json=None
    )
    monkeypatch.setattr(
        gmail.SystemSettings, "get_or_create_settings", AsyncMock(return_value=settings)
    )
    result = await gmail.gmail_oauth_authorize(SimpleNamespace(), db, _user())
    assert result["authorization_url"] == "https://accounts.invalid/auth"
    assert gmail.origin_from_oauth_state(result["state"]) == "https://panel.invalid"
    verifier = gmail.verifier_from_oauth_state(result["state"])
    assert verifier is not None
    callback = await gmail.gmail_oauth_callback(
        SimpleNamespace(), code="code", state=result["state"], db=db
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/settings?gmail_auth=success"
    assert "access" in settings.gmail_token_json
    assert _Flow.redirect_uris == ["https://panel.invalid/api/gmail-oauth/callback"] * 2
    assert _Flow.verifiers == [verifier, verifier]
    assert _Flow.fetch_count == 1
    rejected = await gmail.gmail_oauth_callback(
        SimpleNamespace(), code="code", state="tampered", db=db
    )
    assert rejected.headers["location"] == "/settings?gmail_auth=error"
    assert _Flow.fetch_count == 1

    settings.gmail_credentials_json = "not-json"
    callback = await gmail.gmail_oauth_callback(SimpleNamespace(), code="code", db=db)
    assert callback.headers["location"] == "/settings?gmail_auth=error"


def _install_gmail_flow(monkeypatch, flow_cls: type) -> None:
    flow_module = ModuleType("google_auth_oauthlib.flow")
    flow_module.Flow = flow_cls
    package = ModuleType("google_auth_oauthlib")
    package.flow = flow_module
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", package)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_module)
    monkeypatch.setattr(gmail.settings, "BACKEND_URL", "https://panel.invalid")
    monkeypatch.setattr(gmail.settings, "SECRET_KEY", "s" * 32)


@pytest.mark.asyncio
async def test_gmail_callback_saves_token_when_google_adds_openid_scopes(monkeypatch):
    send = "https://www.googleapis.com/auth/gmail.send"
    granted = [
        "https://www.googleapis.com/auth/userinfo.email",
        send,
        "openid",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]

    class _Flow:
        def __init__(self, code_verifier: str | None = None):
            self.code_verifier = code_verifier
            self.oauth2session = SimpleNamespace(token=None)

        @classmethod
        def from_client_config(cls, *_args, **kwargs):
            return cls(code_verifier=kwargs.get("code_verifier"))

        def fetch_token(self, **_kwargs):
            warning = Warning(
                'Scope has changed from "https://www.googleapis.com/auth/gmail.send" to '
                '"https://www.googleapis.com/auth/userinfo.email '
                "https://www.googleapis.com/auth/gmail.send openid "
                'https://www.googleapis.com/auth/userinfo.profile".'
            )
            warning.new_scope = list(granted)
            warning.token = {
                "access_token": "access-kept",
                "refresh_token": "refresh-kept",
                "expires_at": 1_893_456_000,
                "scope": list(granted),
            }
            raise warning

        @property
        def credentials(self):
            token = self.oauth2session.token or {}
            return SimpleNamespace(
                token=token.get("access_token"),
                refresh_token=token.get("refresh_token"),
                token_uri="https://oauth.invalid/token",
                client_id="client",
                client_secret="secret",
                scopes=[send],
                granted_scopes=token.get("scope"),
            )

    _install_gmail_flow(monkeypatch, _Flow)
    db = _Db()
    settings = SimpleNamespace(
        gmail_credentials_json='{"web": {"client_id": "id"}}', gmail_token_json=None
    )
    monkeypatch.setattr(
        gmail.SystemSettings, "get_or_create_settings", AsyncMock(return_value=settings)
    )
    state = gmail.sign_oauth_state("https://panel.invalid", "a" * 43)
    callback = await gmail.gmail_oauth_callback(SimpleNamespace(), code="code", state=state, db=db)
    assert callback.headers["location"] == "/settings?gmail_auth=success"
    assert settings.gmail_token_json is not None
    saved = json.loads(settings.gmail_token_json)
    assert saved["token"] == "access-kept"
    assert saved["refresh_token"] == "refresh-kept"
    assert send in saved["scopes"]
    assert "openid" in saved["scopes"]
    assert "access-kept" not in callback.headers["location"]


@pytest.mark.asyncio
async def test_gmail_callback_rejects_a_token_that_dropped_gmail_send(monkeypatch):
    class _Flow:
        def __init__(self, code_verifier: str | None = None):
            self.code_verifier = code_verifier
            self.oauth2session = SimpleNamespace(token=None)

        @classmethod
        def from_client_config(cls, *_args, **kwargs):
            return cls(code_verifier=kwargs.get("code_verifier"))

        def fetch_token(self, **_kwargs):
            warning = Warning("Scope has changed")
            warning.new_scope = ["openid"]
            warning.token = {
                "access_token": "dropped",
                "refresh_token": "dropped",
                "expires_at": 1,
                "scope": ["openid"],
            }
            raise warning

        @property
        def credentials(self):
            raise AssertionError("a token without gmail.send must not be stored")

    _install_gmail_flow(monkeypatch, _Flow)
    db = _Db()
    settings = SimpleNamespace(
        gmail_credentials_json='{"web": {"client_id": "id"}}', gmail_token_json=None
    )
    monkeypatch.setattr(
        gmail.SystemSettings, "get_or_create_settings", AsyncMock(return_value=settings)
    )
    state = gmail.sign_oauth_state("https://panel.invalid", "a" * 43)
    rejected = await gmail.gmail_oauth_callback(SimpleNamespace(), code="code", state=state, db=db)
    assert rejected.headers["location"] == "/settings?gmail_auth=error"
    assert settings.gmail_token_json is None


def test_gmail_scope_helpers_accept_only_a_complete_send_token():
    send = "https://www.googleapis.com/auth/gmail.send"
    flow = SimpleNamespace(oauth2session=SimpleNamespace(token=None))

    with pytest.raises(Warning):
        gmail.exchange_gmail_code(SimpleNamespace(), "code")

    def incomplete(**_kwargs):
        warning = Warning("changed")
        warning.new_scope = [send]
        warning.token = {"access_token": "t"}
        raise warning

    flow.fetch_token = incomplete
    with pytest.raises(Warning):
        gmail.exchange_gmail_code(flow, "code")

    def expanded(**_kwargs):
        warning = Warning("changed")
        warning.new_scope = f"openid {send}"
        warning.token = {"access_token": "kept", "expires_at": 1}
        raise warning

    flow.fetch_token = expanded
    gmail.exchange_gmail_code(flow, "code")
    assert flow.oauth2session.token["access_token"] == "kept"

    with pytest.raises(Warning):
        gmail.exchange_gmail_code(SimpleNamespace(fetch_token=expanded), "code")

    assert gmail.stored_gmail_scopes(SimpleNamespace(scopes=[send])) == [send]
    assert gmail.stored_gmail_scopes(SimpleNamespace()) == [send]
    assert gmail._warning_scopes(Warning("x")) == []


def test_gmail_authorize_origin_prefers_the_browser_over_backend_url(monkeypatch):
    monkeypatch.setattr(gmail.settings, "BACKEND_URL", "http://127.0.0.1:8000")
    monkeypatch.setattr(gmail.settings, "SECRET_KEY", "s" * 32)
    request = SimpleNamespace(headers={"x-upkk-public-origin": "https://panel.example:31800/"})
    origin = gmail.authorize_origin(request)
    assert origin == "https://panel.example:31800"
    assert (
        gmail.gmail_redirect_uri(origin) == "https://panel.example:31800/api/gmail-oauth/callback"
    )
    state = gmail.sign_oauth_state(origin, "a" * 43)
    assert gmail.origin_from_oauth_state(state) == origin
    assert gmail.verifier_from_oauth_state(state) == "a" * 43
    assert gmail.origin_from_oauth_state("0" * 64 + ".https://evil.example") is None
    assert gmail.verifier_from_oauth_state(state[:-1] + "b") is None
    assert gmail.normalize_public_origin("https://panel.example/callback") is None
    assert gmail.authorize_origin(SimpleNamespace()) == "http://127.0.0.1:8000"


@pytest.mark.asyncio
async def test_steamcmd_watch_reconnects_and_stops_after_missing_process(monkeypatch):
    server = SimpleNamespace(id=4, session_manager="tmux", game_directory="/srv/cs2")
    records = [{"status": "running", "action": "deploy"}] * 4
    hub = SimpleNamespace(get_current=AsyncMock(side_effect=records))
    monkeypatch.setattr(watch, "server_operation_hub", hub)
    monkeypatch.setattr(watch, "send_deployment_update", AsyncMock())
    monkeypatch.setattr(watch, "find_running_session_manager", AsyncMock(return_value=None))
    monkeypatch.setattr(watch.asyncio, "sleep", AsyncMock())
    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(True, "ok")),
        disconnect=AsyncMock(),
        execute_command=AsyncMock(return_value=(True, "", "")),
    )
    monkeypatch.setattr(watch, "SSHManager", lambda: ssh)
    monkeypatch.setattr(watch, "steamcmd_session_name", lambda _id: "steam-session")
    await watch.maybe_resume_steamcmd_watch(server)
    assert 4 not in watch._WATCHES
    ssh.disconnect.assert_awaited_once()

    # Duplicate watches and inactive operations are no-ops.
    watch._WATCHES.add(4)
    await watch.maybe_resume_steamcmd_watch(server)
    watch._WATCHES.clear()
    hub.get_current.side_effect = None
    hub.get_current.return_value = {"status": "completed", "action": "deploy"}
    await watch.maybe_resume_steamcmd_watch(server)
    assert ssh.connect.await_count == 1


@pytest.mark.asyncio
async def test_steamcmd_watch_capture_output_and_connection_failure(monkeypatch):
    server = SimpleNamespace(id=5, session_manager="screen", game_directory="/srv/cs2")
    monkeypatch.setattr(watch, "send_deployment_update", AsyncMock())
    hub = SimpleNamespace(
        get_current=AsyncMock(side_effect=[{"status": "running", "action": "update"}, None])
    )
    monkeypatch.setattr(watch, "server_operation_hub", hub)
    monkeypatch.setattr(watch, "find_running_session_manager", AsyncMock(return_value="tmux"))
    monkeypatch.setattr(watch, "incremental_console_lines", lambda _old, _new: ["line"])
    monkeypatch.setattr(watch, "latest_console_heartbeat", lambda _capture: "heartbeat")
    monkeypatch.setattr(watch.asyncio, "sleep", AsyncMock())
    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(True, "ok")),
        disconnect=AsyncMock(),
        execute_command=AsyncMock(
            side_effect=[(True, "123\ninvalid", ""), (True, "new output", "")]
        ),
    )
    monkeypatch.setattr(watch, "SSHManager", lambda: ssh)
    await watch._run_watch(server)
    assert any(call.args[1] == "output" for call in watch.send_deployment_update.await_args_list)

    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(False, "offline")), disconnect=AsyncMock()
    )
    monkeypatch.setattr(watch, "SSHManager", lambda: ssh)
    await watch._run_watch(server)
    assert "cannot be polled" in watch.send_deployment_update.await_args.args[2]
