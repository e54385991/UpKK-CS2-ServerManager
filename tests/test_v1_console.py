"""Coverage for the versioned ``/api/v1/servers/{id}/console`` workspace."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.application import create_app
from api.dependencies import get_bearer_or_cookie_user
from modules import get_current_active_user, get_current_user, get_db


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _sample_server(**overrides):
    values = {
        "id": 1,
        "name": "ops-verify",
        "host": "127.0.0.1",
        "session_manager": "tmux",
        "game_directory": "/tmp/cs2-ops-verify",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _client(monkeypatch, *, server=None):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="owner", is_admin=False, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_bearer_or_cookie_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    row = server or _sample_server()

    async def fake_access(*_args, **_kwargs):
        return row

    monkeypatch.setattr("api.routes.v1.console.require_server_access", fake_access)
    return TestClient(app), row, user


def test_v1_console_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/servers/1/console")
    assert response.status_code == 401


def test_v1_console_get_degrades_when_ssh_is_down(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(False, "Connection refused")),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr("api.routes.v1.console.SSHManager", lambda: ssh)

    response = client.get("/api/v1/servers/1/console")
    assert response.status_code == 200
    body = response.json()
    assert body["server_id"] == 1
    assert body["host"] == "127.0.0.1"
    assert body["ssh_ok"] is False
    assert body["game_running"] is False
    assert body["steamcmd_running"] is False
    assert "Connection refused" in body["ssh_error"]
    ssh.disconnect.assert_awaited()


def test_v1_console_get_reports_game_session(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(True, "ok")),
        execute_command=AsyncMock(),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr("api.routes.v1.console.SSHManager", lambda: ssh)
    monkeypatch.setattr(
        "api.routes.v1.console.find_running_session_manager",
        AsyncMock(side_effect=["tmux", None]),
    )

    response = client.get("/api/v1/servers/1/console")
    assert response.status_code == 200
    body = response.json()
    assert body["ssh_ok"] is True
    assert body["game_running"] is True
    assert body["steamcmd_running"] is False
    assert body["session_manager"] == "tmux"
    ssh.disconnect.assert_awaited()


def test_v1_console_pane_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/servers/1/console/pane?kind=steamcmd")
    assert response.status_code == 401


def test_v1_console_pane_degrades_when_ssh_is_down(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(False, "Connection refused")),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr("api.routes.v1.console.SSHManager", lambda: ssh)

    response = client.get("/api/v1/servers/1/console/pane?kind=steamcmd")
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "steamcmd"
    assert body["session_name"] == "cs2steamcmd_1"
    assert body["ssh_ok"] is False
    assert body["running"] is False
    assert body["text"] == ""
    assert "Connection refused" in (body["message"] or "")
    ssh.disconnect.assert_awaited()


def test_v1_console_pane_replaces_invalid_utf8_in_tmux_snapshot(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    snapshot = "Lobby\ufffd player joined"
    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(True, "ok")),
        execute_command=AsyncMock(return_value=(True, snapshot, "")),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr("api.routes.v1.console.SSHManager", lambda: ssh)
    monkeypatch.setattr(
        "api.routes.v1.console.find_running_session_manager",
        AsyncMock(return_value="tmux"),
    )

    response = client.get("/api/v1/servers/1/console/pane?kind=game")
    assert response.status_code == 200
    body = response.json()
    assert body["running"] is True
    assert "Lobby" in body["text"]
    assert "player joined" in body["text"]


def test_v1_console_pane_returns_live_tmux_snapshot(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    snapshot = "Update state (0x61) downloading, progress: 12.4\r"
    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(True, "ok")),
        execute_command=AsyncMock(return_value=(True, snapshot, "")),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr("api.routes.v1.console.SSHManager", lambda: ssh)
    monkeypatch.setattr(
        "api.routes.v1.console.find_running_session_manager",
        AsyncMock(return_value="tmux"),
    )

    response = client.get("/api/v1/servers/1/console/pane?kind=steamcmd")
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "steamcmd"
    assert body["session_name"] == "cs2steamcmd_1"
    assert body["running"] is True
    assert body["ssh_ok"] is True
    assert "progress: 12.4" in body["text"]
    assert body["heartbeat"] == "Update state (0x61) downloading, progress: 12.4"
    ssh.disconnect.assert_awaited()


def test_v1_console_pane_reports_idle_when_session_is_missing(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(True, "ok")),
        execute_command=AsyncMock(),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr("api.routes.v1.console.SSHManager", lambda: ssh)
    monkeypatch.setattr(
        "api.routes.v1.console.find_running_session_manager",
        AsyncMock(return_value=None),
    )

    response = client.get("/api/v1/servers/1/console/pane?kind=game")
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "game"
    assert body["session_name"] == "cs2server_1"
    assert body["running"] is False
    assert body["text"] == ""
    ssh.disconnect.assert_awaited()
