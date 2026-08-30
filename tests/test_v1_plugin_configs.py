"""Coverage for the versioned ``/api/v1/servers/{id}/plugin-configs`` workspace."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.responses import StreamingResponse
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
        "game_directory": "/home/cs2",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _source_payload(**overrides):
    values = {
        "id": 11,
        "path": "cs2/game/csgo/addons/counterstrikesharp/configs",
        "absolute_path": "/home/cs2/cs2/game/csgo/addons/counterstrikesharp/configs",
        "name": "configs",
        "type": "directory",
        "is_default": True,
        "persisted": True,
    }
    values.update(overrides)
    return values


def _file_payload(**overrides):
    values = {
        "path": "cs2/game/csgo/cfg/plugin.cfg",
        "name": "plugin.cfg",
        "format": "cfg",
        "revision": "a" * 64,
        "content": "setting 1\n",
        "visual_supported": True,
        "parse_error": None,
        "fields": [
            {
                "id": "setting",
                "key": "setting",
                "group": "Root",
                "kind": "integer",
                "value": 1,
                "line": 1,
                "comment": "",
            }
        ],
        "message": None,
    }
    values.update(overrides)
    return values


def _client(monkeypatch, *, server=None):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="owner", is_admin=False, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    row = server or _sample_server()

    async def fake_access(*_args, **_kwargs):
        return row

    monkeypatch.setattr("api.routes.v1.plugin_configs.require_server_access", fake_access)
    return TestClient(app), row, user


def test_v1_plugin_configs_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/servers/1/plugin-configs/sources")
    assert response.status_code == 401


def test_v1_plugin_configs_lists_sources_without_ssh(monkeypatch):
    client, _server, _user = _client(monkeypatch)

    async def fake_list(*_args, **_kwargs):
        return {
            "game_directory": "/home/cs2",
            "sources": [
                _source_payload(),
                _source_payload(id=12, path="cs2/game/csgo/cfg", name="cfg"),
            ],
        }

    monkeypatch.setattr("api.routes.v1.plugin_configs.legacy.list_sources", fake_list)

    response = client.get("/api/v1/servers/1/plugin-configs/sources")
    assert response.status_code == 200
    body = response.json()
    assert body["server_id"] == 1
    assert body["game_directory"] == "/home/cs2"
    assert [item["name"] for item in body["sources"]] == ["configs", "cfg"]
    assert body["sources"][0]["persisted"] is True
    assert "ssh_password" not in body
    assert "rcon_password" not in body


def test_v1_plugin_configs_create_wraps_legacy(monkeypatch):
    client, _server, _user = _client(monkeypatch)

    async def fake_create(server_id, request, *_args, **_kwargs):
        assert server_id == 1
        assert request.path == "configs/custom"
        return _source_payload(id=42, path="configs/custom", name="custom", is_default=False)

    monkeypatch.setattr("api.routes.v1.plugin_configs.legacy.create_source", fake_create)

    response = client.post(
        "/api/v1/servers/1/plugin-configs/sources",
        json={"path": "configs/custom"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == 42
    assert body["path"] == "configs/custom"
    assert body["persisted"] is True


def test_v1_plugin_configs_delete_wraps_legacy(monkeypatch):
    client, _server, _user = _client(monkeypatch)

    async def fake_delete(server_id, source_id, *_args, **_kwargs):
        assert server_id == 1
        assert source_id == 11
        return {"success": True}

    monkeypatch.setattr("api.routes.v1.plugin_configs.legacy.delete_source", fake_delete)

    response = client.delete("/api/v1/servers/1/plugin-configs/sources/11")
    assert response.status_code == 200
    assert response.json() == {"success": True}


def test_v1_plugin_configs_restore_default_wraps_legacy(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    first = _source_payload()
    second = _source_payload(id=12, path="cs2/game/csgo/cfg", name="cfg")

    async def fake_restore(*_args, **_kwargs):
        return {**first, "sources": [first, second]}

    monkeypatch.setattr(
        "api.routes.v1.plugin_configs.legacy.restore_default_source",
        fake_restore,
    )

    response = client.post("/api/v1/servers/1/plugin-configs/sources/restore-default")
    assert response.status_code == 200
    body = response.json()
    assert body["server_id"] == 1
    assert [item["path"] for item in body["sources"]] == [
        "cs2/game/csgo/addons/counterstrikesharp/configs",
        "cs2/game/csgo/cfg",
    ]


def test_v1_plugin_configs_browse_wraps_legacy(monkeypatch):
    client, _server, _user = _client(monkeypatch)

    async def fake_browse(server_id, path, *_args, **_kwargs):
        assert path == "cs2/game/csgo"
        return {
            "path": "cs2/game/csgo",
            "items": [
                {
                    "name": "cfg",
                    "path": "cs2/game/csgo/cfg",
                    "type": "directory",
                    "selectable": True,
                },
                {"name": "link", "type": "symlink", "selectable": False},
            ],
        }

    monkeypatch.setattr("api.routes.v1.plugin_configs.legacy.browse_source_path", fake_browse)

    response = client.get(
        "/api/v1/servers/1/plugin-configs/browse",
        params={"path": "cs2/game/csgo"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "cs2/game/csgo"
    assert body["items"][0]["type"] == "directory"
    assert body["items"][1]["selectable"] is False


def test_v1_plugin_configs_scan_streams_ndjson(monkeypatch):
    client, _server, _user = _client(monkeypatch)

    async def stream():
        yield json.dumps({"type": "start"}) + "\n"
        yield json.dumps({"type": "file", "file": {"tree_path": "plugin.cfg"}}) + "\n"
        yield json.dumps({"type": "complete", "count": 1, "truncated": False}) + "\n"

    async def fake_scan(*_args, **_kwargs):
        return StreamingResponse(stream(), media_type="application/x-ndjson")

    monkeypatch.setattr("api.routes.v1.plugin_configs.legacy.load_source_files", fake_scan)

    response = client.post("/api/v1/servers/1/plugin-configs/sources/11/scan")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == ["start", "file", "complete"]


def test_v1_plugin_configs_file_round_trip(monkeypatch):
    client, _server, _user = _client(monkeypatch)

    async def fake_get(*_args, **_kwargs):
        return _file_payload()

    async def fake_save(server_id, source_id, request, *_args, **_kwargs):
        assert request.mode == "raw"
        assert request.content == "setting 2\n"
        return _file_payload(content="setting 2\n", message="Configuration saved.")

    monkeypatch.setattr("api.routes.v1.plugin_configs.legacy.get_config_file", fake_get)
    monkeypatch.setattr("api.routes.v1.plugin_configs.legacy.save_config_file", fake_save)

    loaded = client.get(
        "/api/v1/servers/1/plugin-configs/sources/11/file",
        params={"path": "cs2/game/csgo/cfg/plugin.cfg"},
    )
    assert loaded.status_code == 200
    assert loaded.json()["fields"][0]["value"] == 1

    saved = client.put(
        "/api/v1/servers/1/plugin-configs/sources/11/file",
        json={
            "path": "cs2/game/csgo/cfg/plugin.cfg",
            "expected_revision": "a" * 64,
            "mode": "raw",
            "content": "setting 2\n",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["content"] == "setting 2\n"
    assert "Configuration saved" in saved.json()["message"]
