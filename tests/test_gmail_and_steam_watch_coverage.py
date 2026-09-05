"""覆盖 Gmail 认证边界和 SteamCMD 断线观察器的本地状态路径。"""

from __future__ import annotations

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
    assert "error" in response.headers["location"]


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

        @classmethod
        def from_client_config(cls, *_args, **_kwargs):
            return cls()

        def authorization_url(self, **_kwargs):
            return "https://accounts.invalid/auth", "state"

        def fetch_token(self, **_kwargs):
            return None

    flow_module = ModuleType("google_auth_oauthlib.flow")
    flow_module.Flow = _Flow
    package = ModuleType("google_auth_oauthlib")
    package.flow = flow_module
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", package)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_module)
    monkeypatch.setattr(gmail.settings, "BACKEND_URL", "https://panel.invalid")
    db = _Db()
    settings = SimpleNamespace(
        gmail_credentials_json='{"web": {"client_id": "id"}}', gmail_token_json=None
    )
    monkeypatch.setattr(
        gmail.SystemSettings, "get_or_create_settings", AsyncMock(return_value=settings)
    )
    result = await gmail.gmail_oauth_authorize(SimpleNamespace(), db, _user())
    assert result == {"authorization_url": "https://accounts.invalid/auth", "state": "state"}
    callback = await gmail.gmail_oauth_callback(SimpleNamespace(), code="code", db=db)
    assert callback.status_code == 302
    assert "success" in callback.headers["location"]
    assert "access" in settings.gmail_token_json

    settings.gmail_credentials_json = "not-json"
    callback = await gmail.gmail_oauth_callback(SimpleNamespace(), code="code", db=db)
    assert "error" in callback.headers["location"]


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
