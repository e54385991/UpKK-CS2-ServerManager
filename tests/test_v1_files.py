"""Coverage for the versioned ``/api/v1/servers/{id}/files`` workspace."""

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


def _sample_server(**overrides):
    values = {
        "id": 1,
        "name": "ops-verify",
        "game_directory": "/tmp/cs2-ops-verify",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _client(monkeypatch, *, server=None):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="owner", is_admin=False, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    row = server or _sample_server()

    async def fake_access(*_args, **_kwargs):
        return row

    monkeypatch.setattr("api.routes.v1.files.require_server_access", fake_access)
    return TestClient(app), row, user


def test_v1_files_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/servers/1/files")
    assert response.status_code == 401


def test_v1_files_get_degrades_when_ssh_is_down(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    ssh = SimpleNamespace(
        list_directory=AsyncMock(return_value=(False, [], "Connection refused")),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr("api.routes.v1.files.SSHManager", lambda: ssh)

    response = client.get("/api/v1/servers/1/files")
    assert response.status_code == 200
    body = response.json()
    assert body["server_id"] == 1
    assert body["ssh_ok"] is False
    assert body["files"] == []
    assert body["root"] == "/tmp/cs2-ops-verify"
    assert body["path"] == "/tmp/cs2-ops-verify"
    assert "Connection refused" in body["ssh_error"]
    ssh.disconnect.assert_awaited()


def test_v1_files_get_rejects_path_outside_root(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    response = client.get("/api/v1/servers/1/files", params={"path": "/etc/passwd"})
    assert response.status_code == 403


def test_v1_files_get_lists_entries(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    ssh = SimpleNamespace(
        list_directory=AsyncMock(
            return_value=(
                True,
                [
                    {
                        "name": "cfg",
                        "path": "/tmp/cs2-ops-verify/cfg",
                        "type": "directory",
                        "size": 0,
                        "modified": 1.0,
                        "permissions": "755",
                        "is_symlink": False,
                    },
                    {
                        "name": "server.cfg",
                        "path": "/tmp/cs2-ops-verify/server.cfg",
                        "type": "file",
                        "size": 12,
                        "modified": 2.0,
                        "permissions": "644",
                        "is_symlink": False,
                    },
                ],
                "",
            )
        ),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr("api.routes.v1.files.SSHManager", lambda: ssh)

    response = client.get(
        "/api/v1/servers/1/files",
        params={"path": "/tmp/cs2-ops-verify"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ssh_ok"] is True
    assert [item["name"] for item in body["files"]] == ["cfg", "server.cfg"]
    assert body["files"][0]["type"] == "directory"
    assert body["files"][1]["size"] == 12


def test_v1_files_mkdir_wraps_legacy(monkeypatch):
    client, _server, _user = _client(monkeypatch)

    async def fake_mkdir(server_id, path, request, db, current_user):
        return {
            "success": True,
            "message": "Directory created successfully",
            "path": f"{path}/{request.name}",
        }

    monkeypatch.setattr("api.routes.v1.files.legacy_files.create_directory", fake_mkdir)
    response = client.post(
        "/api/v1/servers/1/files/mkdir",
        params={"path": "/tmp/cs2-ops-verify"},
        json={"name": "maps"},
    )
    assert response.status_code == 200
    assert response.json()["path"] == "/tmp/cs2-ops-verify/maps"


def test_v1_files_content_round_trip(monkeypatch):
    client, _server, _user = _client(monkeypatch)

    async def fake_get(*_args, **_kwargs):
        return {"path": "/tmp/cs2-ops-verify/server.cfg", "content": "hostname test"}

    async def fake_put(*_args, **_kwargs):
        return {"success": True, "message": "File updated successfully"}

    monkeypatch.setattr("api.routes.v1.files.legacy_files.get_file_content", fake_get)
    monkeypatch.setattr("api.routes.v1.files.legacy_files.update_file_content", fake_put)

    get_response = client.get(
        "/api/v1/servers/1/files/content",
        params={"path": "/tmp/cs2-ops-verify/server.cfg"},
    )
    assert get_response.status_code == 200
    assert get_response.json()["content"] == "hostname test"

    put_response = client.put(
        "/api/v1/servers/1/files/content",
        params={"path": "/tmp/cs2-ops-verify/server.cfg"},
        json={"content": "hostname next"},
    )
    assert put_response.status_code == 200
    assert put_response.json()["success"] is True
