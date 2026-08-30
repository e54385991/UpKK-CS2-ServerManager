"""Coverage for the versioned ``/api/v1/servers/{id}/plugin-updates`` workspace."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _client(monkeypatch):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="owner", is_admin=False, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app), user


def test_v1_plugin_updates_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/servers/2/plugin-updates")
    assert response.status_code == 401


def test_v1_plugin_updates_workspace_and_run(monkeypatch):
    client, _user = _client(monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.plugin_updates.legacy.get_configuration",
        AsyncMock(
            return_value=SimpleNamespace(
                enable_plugin_auto_update=True,
                plugin_update_check_interval_hours=6.0,
                last_plugin_update_check=None,
                enable_plugin_post_update_commands=False,
                plugin_post_update_command_ids=[],
                plugins=[
                    SimpleNamespace(
                        id=3,
                        server_id=2,
                        source_type="github",
                        source_key="owner/repo",
                        display_name="Demo",
                        repo_url="https://github.com/owner/repo",
                        market_plugin_id=None,
                        framework_key=None,
                        installed_version="1.0.0",
                        latest_version="1.1.0",
                        auto_update_enabled=True,
                        last_status="ok",
                        last_error=None,
                        last_check_at=None,
                        last_update_at=None,
                    )
                ],
            )
        ),
    )
    listed = client.get("/api/v1/servers/2/plugin-updates")
    assert listed.status_code == 200
    body = listed.json()
    assert body["enable_plugin_auto_update"] is True
    assert body["plugins"][0]["display_name"] == "Demo"
    assert body["plugins"][0]["exclude_dirs"] == []
    assert body["plugins"][0]["exclude_files"] == []

    monkeypatch.setattr(
        "api.routes.v1.plugin_updates.legacy.run_now",
        AsyncMock(
            return_value=SimpleNamespace(success=True, message="Plugin update check started")
        ),
    )
    started = client.post("/api/v1/servers/2/plugin-updates/run")
    assert started.status_code == 202
    assert started.json()["success"] is True


def test_v1_plugin_updates_patch_exclusions(monkeypatch):
    client, _user = _client(monkeypatch)
    captured = {}

    async def fake_update(_server_id, plugin_id, body, _db, _user):
        captured["plugin_id"] = plugin_id
        captured["exclude_dirs"] = body.exclude_dirs
        captured["exclude_files"] = body.exclude_files
        return SimpleNamespace(
            id=plugin_id,
            server_id=2,
            source_type="github",
            source_key="owner/repo",
            display_name="Demo",
            repo_url="https://github.com/owner/repo",
            market_plugin_id=None,
            framework_key=None,
            installed_version="1.0.0",
            latest_version="1.1.0",
            auto_update_enabled=True,
            last_status="ok",
            last_error=None,
            last_check_at=None,
            last_update_at=None,
            exclude_dirs=body.exclude_dirs,
            exclude_files=body.exclude_files,
        )

    monkeypatch.setattr(
        "api.routes.v1.plugin_updates.legacy.update_plugin",
        fake_update,
    )
    response = client.patch(
        "/api/v1/servers/2/plugin-updates/plugins/3",
        json={"exclude_dirs": ["configs"], "exclude_files": ["*.json"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert captured["exclude_dirs"] == ["configs"]
    assert captured["exclude_files"] == ["*.json"]
    assert body["exclude_dirs"] == ["configs"]
    assert body["exclude_files"] == ["*.json"]
    assert body["backup_before_update"] is False
    assert body["restart_after_update"] is False


def _registered_plugin(**overrides):
    values = {
        "id": 11,
        "server_id": 2,
        "source_type": "github",
        "source_key": "https://github.com/owner/repo",
        "display_name": "Demo",
        "repo_url": "https://github.com/owner/repo",
        "market_plugin_id": None,
        "framework_key": None,
        "installed_version": "1.2.0",
        "latest_version": None,
        "auto_update_enabled": False,
        "last_status": None,
        "last_error": None,
        "last_check_at": None,
        "last_update_at": None,
        "exclude_dirs": ["configs"],
        "exclude_files": ["*.json"],
        "backup_before_update": False,
        "restart_after_update": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_v1_plugin_updates_register_plugin(monkeypatch):
    client, _user = _client(monkeypatch)
    captured = {}

    async def fake_register(_server_id, body, _db, _user):
        captured["source_type"] = body.source_type
        captured["repo_url"] = body.repo_url
        captured["asset_glob"] = body.asset_glob
        captured["exclude_dirs"] = body.exclude_dirs
        captured["auto_update_enabled"] = body.auto_update_enabled
        return _registered_plugin(
            source_type=body.source_type,
            repo_url=body.repo_url,
            display_name=body.display_name,
            installed_version=body.installed_version,
            exclude_dirs=body.exclude_dirs,
            exclude_files=body.exclude_files,
        )

    monkeypatch.setattr(
        "api.routes.v1.plugin_updates.legacy.register_plugin",
        fake_register,
    )
    response = client.post(
        "/api/v1/servers/2/plugin-updates/plugins",
        json={
            "source_type": "github",
            "display_name": "Demo",
            "repo_url": "https://github.com/owner/repo",
            "installed_version": "1.2.0",
            "asset_glob": "demo-*.zip",
            "exclude_dirs": ["configs"],
            "exclude_files": ["*.json"],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert captured["source_type"] == "github"
    assert captured["repo_url"] == "https://github.com/owner/repo"
    assert captured["asset_glob"] == "demo-*.zip"
    assert captured["exclude_dirs"] == ["configs"]
    assert captured["auto_update_enabled"] is False
    assert body["id"] == 11
    assert body["display_name"] == "Demo"
    assert body["exclude_dirs"] == ["configs"]
    assert body["backup_before_update"] is False


def test_v1_plugin_updates_register_framework(monkeypatch):
    client, _user = _client(monkeypatch)
    captured = {}

    async def fake_register(_server_id, body, _db, _user):
        captured["source_type"] = body.source_type
        captured["framework_key"] = body.framework_key
        return _registered_plugin(
            source_type="framework",
            source_key="metamod",
            display_name="Metamod:Source",
            repo_url="https://github.com/alliedmodders/metamod-source",
            framework_key=body.framework_key,
            exclude_dirs=[],
            exclude_files=[],
        )

    monkeypatch.setattr(
        "api.routes.v1.plugin_updates.legacy.register_plugin",
        fake_register,
    )
    response = client.post(
        "/api/v1/servers/2/plugin-updates/plugins",
        json={
            "source_type": "framework",
            "display_name": "metamod",
            "framework_key": "metamod",
        },
    )
    assert response.status_code == 201
    assert captured["source_type"] == "framework"
    assert captured["framework_key"] == "metamod"
    assert response.json()["framework_key"] == "metamod"


def test_v1_plugin_updates_unregister_plugin(monkeypatch):
    client, _user = _client(monkeypatch)
    captured = {}

    async def fake_unmanage(server_id, plugin_id, _db, _user):
        captured["server_id"] = server_id
        captured["plugin_id"] = plugin_id
        return SimpleNamespace(
            success=True,
            message="Plugin is no longer managed; remote files were not removed",
        )

    monkeypatch.setattr(
        "api.routes.v1.plugin_updates.legacy.unmanage_plugin",
        fake_unmanage,
    )
    response = client.delete("/api/v1/servers/2/plugin-updates/plugins/11")
    assert response.status_code == 200
    body = response.json()
    assert captured == {"server_id": 2, "plugin_id": 11}
    assert body["success"] is True
    assert "not removed" in body["message"]


def test_v1_plugin_updates_non_owner_cannot_register(monkeypatch):
    client, _user = _client(monkeypatch)

    async def deny(*_args, **_kwargs):
        raise HTTPException(status_code=404, detail="Server not found")

    monkeypatch.setattr(
        "api.routes.v1.plugin_updates.legacy.register_plugin",
        deny,
    )
    response = client.post(
        "/api/v1/servers/9/plugin-updates/plugins",
        json={
            "source_type": "github",
            "display_name": "Stolen",
            "repo_url": "https://github.com/owner/repo",
            "asset_glob": "demo-*.zip",
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Server not found"


def test_v1_plugin_updates_non_owner_cannot_unregister(monkeypatch):
    client, _user = _client(monkeypatch)

    async def deny(*_args, **_kwargs):
        raise HTTPException(status_code=404, detail="Server not found")

    monkeypatch.setattr(
        "api.routes.v1.plugin_updates.legacy.unmanage_plugin",
        deny,
    )
    response = client.delete("/api/v1/servers/9/plugin-updates/plugins/11")
    assert response.status_code == 404
    assert response.json()["detail"] == "Server not found"


def test_v1_plugin_updates_status_formats_service_logs(monkeypatch):
    client, _user = _client(monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.plugin_updates.legacy.get_run_status",
        AsyncMock(
            return_value={
                "state": "running",
                "phase": "checking",
                "message": "Working",
                "current": 1,
                "total": 4,
                "logs": [
                    {"time": "2026-08-30T12:00:00+00:00", "message": "started"},
                    {"message": "no timestamp"},
                    "plain line",
                ],
                "started_at": "2026-08-30T12:00:00+00:00",
                "finished_at": None,
            }
        ),
    )
    response = client.get("/api/v1/servers/2/plugin-updates/status")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "running"
    assert body["phase"] == "checking"
    assert body["current"] == 1
    assert body["total"] == 4
    assert body["logs"] == [
        "2026-08-30T12:00:00+00:00 started",
        "no timestamp",
        "plain line",
    ]


def test_status_log_line_keeps_webpage_time_and_message():
    from api.routes.v1.plugin_updates import _status_log_line

    assert (
        _status_log_line({"time": "2026-08-30T12:00:00", "message": "checking"})
        == "2026-08-30T12:00:00 checking"
    )
    assert _status_log_line({"message": "only"}) == "only"
    assert _status_log_line("plain") == "plain"


def test_v1_plugin_updates_register_rejects_traversal(monkeypatch):
    client, _user = _client(monkeypatch)
    response = client.post(
        "/api/v1/servers/2/plugin-updates/plugins",
        json={
            "source_type": "github",
            "display_name": "Demo",
            "repo_url": "https://github.com/owner/repo",
            "asset_glob": "demo-*.zip",
            "exclude_dirs": ["../etc"],
        },
    )
    assert response.status_code == 422
