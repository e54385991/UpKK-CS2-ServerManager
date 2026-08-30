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


def test_v1_files_get_missing_path_keeps_ssh_ok(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    ssh = SimpleNamespace(
        list_directory=AsyncMock(return_value=(False, [], "No such file")),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr("api.routes.v1.files.SSHManager", lambda: ssh)

    response = client.get(
        "/api/v1/servers/1/files",
        params={"path": "/tmp/cs2-ops-verify/game/csgo/addons/counterstrikesharp"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ssh_ok"] is True
    assert body["files"] == []
    assert body["ssh_error"] is None
    assert "No such file" in (body["message"] or "")
    ssh.disconnect.assert_awaited()


def test_v1_files_get_rewrites_nested_cs2_game_path(monkeypatch):
    client, server, _user = _client(
        monkeypatch,
        server=_sample_server(game_directory="/home/cs2server/cs2ze"),
    )
    listed = []

    async def fake_list(path, _server):
        listed.append(path)
        if "/cs2/game/" in path:
            return False, [], "No such file"
        return (
            True,
            [
                {
                    "name": "configs",
                    "path": f"{path}/configs",
                    "type": "directory",
                    "size": 0,
                    "modified": 1.0,
                    "permissions": "755",
                    "is_symlink": False,
                }
            ],
            "",
        )

    ssh = SimpleNamespace(list_directory=fake_list, disconnect=AsyncMock())
    monkeypatch.setattr("api.routes.v1.files.SSHManager", lambda: ssh)

    response = client.get(
        "/api/v1/servers/1/files",
        params={"path": "/home/cs2server/cs2ze/cs2/game/csgo/addons/counterstrikesharp"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ssh_ok"] is True
    assert body["path"] == "/home/cs2server/cs2ze/game/csgo/addons/counterstrikesharp"
    assert [item["name"] for item in body["files"]] == ["configs"]
    assert listed == [
        "/home/cs2server/cs2ze/cs2/game/csgo/addons/counterstrikesharp",
        "/home/cs2server/cs2ze/game/csgo/addons/counterstrikesharp",
    ]
    assert server.game_directory == "/home/cs2server/cs2ze"


def test_v1_files_get_rewrites_flat_game_path_to_nested_cs2(monkeypatch):
    client, server, _user = _client(
        monkeypatch,
        server=_sample_server(game_directory="/home/cs2server/cs2ze"),
    )
    listed = []

    async def fake_list(path, _server):
        listed.append(path)
        if "/cs2/game/" not in path:
            return False, [], "No such file"
        return (
            True,
            [
                {
                    "name": "server.cfg",
                    "path": f"{path}/server.cfg",
                    "type": "file",
                    "size": 12,
                    "modified": 1.0,
                    "permissions": "644",
                    "is_symlink": False,
                }
            ],
            "",
        )

    ssh = SimpleNamespace(list_directory=fake_list, disconnect=AsyncMock())
    monkeypatch.setattr("api.routes.v1.files.SSHManager", lambda: ssh)

    response = client.get(
        "/api/v1/servers/1/files",
        params={"path": "/home/cs2server/cs2ze/game/csgo/cfg"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ssh_ok"] is True
    assert body["path"] == "/home/cs2server/cs2ze/cs2/game/csgo/cfg"
    assert [item["name"] for item in body["files"]] == ["server.cfg"]
    assert listed == [
        "/home/cs2server/cs2ze/game/csgo/cfg",
        "/home/cs2server/cs2ze/cs2/game/csgo/cfg",
    ]
    assert server.game_directory == "/home/cs2server/cs2ze"


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


def test_v1_files_copy_wraps_legacy(monkeypatch):
    client, _server, _user = _client(monkeypatch)

    async def fake_copy(server_id, request, db, current_user):
        return {
            "success": True,
            "message": "Copied successfully",
            "paths": [f"{request.destination}/{name}" for name in ("server.cfg",)],
            "path": f"{request.destination}/server.cfg",
        }

    monkeypatch.setattr("api.routes.v1.files.legacy_files.copy_paths", fake_copy)
    response = client.post(
        "/api/v1/servers/1/files/copy",
        json={
            "sources": ["/tmp/cs2-ops-verify/server.cfg"],
            "destination": "/tmp/cs2-ops-verify/cfg",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["paths"] == ["/tmp/cs2-ops-verify/cfg/server.cfg"]


def test_v1_files_upload_preserves_folder_relative_path(monkeypatch):
    client, server, _user = _client(monkeypatch)
    captured: dict[str, str] = {}

    async def fake_access(*_args, **_kwargs):
        return server

    ssh = SimpleNamespace(
        upload_file=AsyncMock(
            side_effect=lambda local, remote, _srv: captured.update(remote=remote) or (True, "")
        ),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr("api.routes.file_manager.files.get_server_for_user", fake_access)
    monkeypatch.setattr("api.routes.file_manager.files.SSHManager", lambda: ssh)

    response = client.post(
        "/api/v1/servers/1/files/upload",
        params={"path": "/tmp/cs2-ops-verify", "relative_path": "plugin/cfg/server.cfg"},
        files={"file": ("server.cfg", b"hostname test", "text/plain")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["path"] == "/tmp/cs2-ops-verify/plugin/cfg/server.cfg"
    assert captured["remote"] == "/tmp/cs2-ops-verify/plugin/cfg/server.cfg"
    ssh.disconnect.assert_awaited()


def test_v1_files_copy_rejects_empty_sources(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    response = client.post(
        "/api/v1/servers/1/files/copy",
        json={"sources": [], "destination": "/tmp/cs2-ops-verify"},
    )
    assert response.status_code == 422
