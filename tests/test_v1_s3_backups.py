"""Coverage for the versioned ``/api/v1/servers/{id}/s3-backups`` contract."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db
from modules.models.servers import ServerStatus


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _sample_server(**overrides):
    values = {
        "id": 7,
        "user_id": 1,
        "name": "bravo",
        "host": "10.0.0.8",
        "game_port": 27015,
        "status": ServerStatus.STOPPED,
        "game_directory": "/home/steam/cs2",
        "ssh_password": "should-never-leak",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _owner(**overrides):
    values = {
        "id": 1,
        "username": "owner",
        "is_admin": False,
        "is_active": True,
        "s3_enabled": True,
        "s3_bucket": "backups",
        "s3_access_key_id": "AKIASECRET",
        "s3_secret_access_key": "s3-secret-key",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _client(monkeypatch, *, admin: bool = False):
    app = create_app(lifespan=None)
    user = SimpleNamespace(
        id=1,
        username="owner",
        is_admin=admin,
        is_active=True,
        s3_secret_access_key="s3-secret-key",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app), user


def test_v1_s3_backups_require_authentication():
    client = TestClient(create_app(lifespan=None))
    assert client.get("/api/v1/servers/7/s3-backups").status_code == 401
    assert (
        client.post(
            "/api/v1/servers/7/s3-backups/restore",
            json={"object_key": "user-1/server-7/backup.tar.gz"},
        ).status_code
        == 401
    )


def test_v1_list_s3_backups_empty_when_unconfigured(monkeypatch):
    client, _user = _client(monkeypatch)
    server = _sample_server()
    owner = _owner(s3_enabled=False, s3_secret_access_key=None)

    async def fake_access(_db, server_id, _user):
        assert server_id == 7
        return server

    async def fake_owner(_db, found, current_user):
        assert found is server
        return owner

    async def fake_list(found_owner, found_server):
        assert found_owner is owner
        assert found_server is server
        return True, [], "S3-compatible storage is not configured."

    monkeypatch.setattr("api.routes.v1.s3_backups.require_server_access", fake_access)
    monkeypatch.setattr("api.routes.v1.s3_backups.get_server_owner_user", fake_owner)
    monkeypatch.setattr(
        "api.routes.v1.s3_backups.s3_backup_service.is_configured",
        lambda _owner: False,
    )
    monkeypatch.setattr("api.routes.v1.s3_backups.s3_backup_service.list_backups", fake_list)

    response = client.get("/api/v1/servers/7/s3-backups")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["items"] == []
    assert "not configured" in body["message"]
    assert "s3-secret-key" not in str(body)
    assert "AKIASECRET" not in str(body)


def test_v1_list_s3_backups_projects_objects_without_credentials(monkeypatch):
    client, _user = _client(monkeypatch)
    server = _sample_server()
    owner = _owner()
    stamped = datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc)

    async def fake_access(*_args, **_kwargs):
        return server

    async def fake_owner(*_args, **_kwargs):
        return owner

    async def fake_list(*_args, **_kwargs):
        return (
            True,
            [
                {
                    "key": "user-1/server-7/plugins-2026.tar.gz",
                    "filename": "plugins-2026.tar.gz",
                    "size": 2048,
                    "last_modified": stamped,
                    "etag": "abc123",
                }
            ],
            "",
        )

    monkeypatch.setattr("api.routes.v1.s3_backups.require_server_access", fake_access)
    monkeypatch.setattr("api.routes.v1.s3_backups.get_server_owner_user", fake_owner)
    monkeypatch.setattr(
        "api.routes.v1.s3_backups.s3_backup_service.is_configured",
        lambda _owner: True,
    )
    monkeypatch.setattr("api.routes.v1.s3_backups.s3_backup_service.list_backups", fake_list)

    response = client.get("/api/v1/servers/7/s3-backups")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["items"][0]["key"] == "user-1/server-7/plugins-2026.tar.gz"
    assert body["items"][0]["filename"] == "plugins-2026.tar.gz"
    assert body["items"][0]["size"] == 2048
    assert "s3-secret-key" not in str(body)
    assert "AKIASECRET" not in str(body)
    assert "should-never-leak" not in str(body)
    assert "access_key" not in str(body).lower()
    assert "secret" not in str(body).lower()


def test_v1_restore_s3_backup_returns_202(monkeypatch):
    client, user = _client(monkeypatch)
    server = _sample_server()
    owner = _owner()
    captured = {}

    async def fake_access(*_args, **_kwargs):
        return server

    async def fake_owner(*_args, **_kwargs):
        return owner

    async def fake_enqueue(*, server_id, object_key, actor_user_id):
        captured["server_id"] = server_id
        captured["object_key"] = object_key
        captured["actor"] = actor_user_id
        return {
            "operation_id": "op-restore-1",
            "server_id": server_id,
            "action": "s3_restore",
            "status": "queued",
            "success": None,
            "message": None,
            "server_status": None,
            "started_at": datetime.now(timezone.utc),
            "completed_at": None,
            "actor_user_id": actor_user_id,
        }

    monkeypatch.setattr("api.routes.v1.s3_backups.require_server_access", fake_access)
    monkeypatch.setattr("api.routes.v1.s3_backups.get_server_owner_user", fake_owner)
    monkeypatch.setattr(
        "api.routes.v1.s3_backups.s3_backup_service.is_configured",
        lambda _owner: True,
    )
    monkeypatch.setattr(
        "api.routes.v1.s3_backups.s3_backup_service.validate_object_key",
        lambda _owner, _server, key: key == "user-1/server-7/plugins-2026.tar.gz",
    )
    monkeypatch.setattr("api.routes.v1.s3_backups.enqueue_s3_restore", fake_enqueue)
    monkeypatch.setattr(
        "api.routes.v1.s3_backups.reject_stuck_lock_unless_active",
        AsyncMock(return_value=None),
    )

    response = client.post(
        "/api/v1/servers/7/s3-backups/restore",
        json={"object_key": "user-1/server-7/plugins-2026.tar.gz"},
    )
    assert response.status_code == 202
    body = response.json()
    assert captured["object_key"] == "user-1/server-7/plugins-2026.tar.gz"
    assert captured["actor"] == user.id
    assert body["action"] == "s3_restore"
    assert body["status"] == "queued"
    assert body["stream_url"] == "/api/v1/servers/7/operations/op-restore-1/events"
    assert "s3-secret-key" not in str(body)


def test_v1_s3_backups_hide_other_users_servers(monkeypatch):
    client, _user = _client(monkeypatch)

    async def fake_access(_db, server_id, _user):
        assert server_id == 9
        raise HTTPException(status_code=404, detail="Server not found")

    monkeypatch.setattr("api.routes.v1.s3_backups.require_server_access", fake_access)
    response = client.get("/api/v1/servers/9/s3-backups")
    assert response.status_code == 404
    assert "Server not found" in response.json()["detail"]


def test_v1_restore_s3_backup_rejects_foreign_key(monkeypatch):
    client, _user = _client(monkeypatch)

    async def fake_access(*_args, **_kwargs):
        return _sample_server()

    async def fake_owner(*_args, **_kwargs):
        return _owner()

    monkeypatch.setattr("api.routes.v1.s3_backups.require_server_access", fake_access)
    monkeypatch.setattr("api.routes.v1.s3_backups.get_server_owner_user", fake_owner)
    monkeypatch.setattr(
        "api.routes.v1.s3_backups.s3_backup_service.is_configured",
        lambda _owner: True,
    )
    monkeypatch.setattr(
        "api.routes.v1.s3_backups.s3_backup_service.validate_object_key",
        lambda *_args, **_kwargs: False,
    )

    response = client.post(
        "/api/v1/servers/7/s3-backups/restore",
        json={"object_key": "user-2/server-99/stolen.tar.gz"},
    )
    assert response.status_code == 403
    assert "does not belong" in response.json()["detail"]
