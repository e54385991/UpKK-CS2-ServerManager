"""Coverage for the versioned ``/api/v1/servers/{id}/cleanup`` contract."""

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


def _client():
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="owner", is_admin=False, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app)


def test_v1_cleanup_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    assert client.get("/api/v1/servers/7/cleanup/scan").status_code == 401
    assert (
        client.post(
            "/api/v1/servers/7/cleanup/delete",
            json={"mode": "safe"},
        ).status_code
        == 401
    )


def test_v1_cleanup_scan_projects_candidates(monkeypatch):
    client = _client()

    async def fake_scan(*_args, **_kwargs):
        return {
            "safe_items": [
                {
                    "path": "cs2/game/csgo/logs/error.log",
                    "name": "error.log",
                    "type": "file",
                    "size": 128,
                    "category": "logs",
                    "reason": "log file",
                    "danger_level": "safe",
                }
            ],
            "archive_items": [],
            "workshop_summary": {
                "path": "cs2/steamapps/workshop",
                "item_count": 2,
                "size": 4096,
            },
            "total_size": 4224,
        }

    monkeypatch.setattr(
        "api.routes.v1.cleanup.legacy.scan_server_cleanup",
        fake_scan,
    )
    response = client.get("/api/v1/servers/7/cleanup/scan")
    assert response.status_code == 200
    body = response.json()
    assert body["total_size"] == 4224
    assert body["safe_items"][0]["path"] == "cs2/game/csgo/logs/error.log"
    assert body["workshop_summary"]["item_count"] == 2
    assert "ssh_password" not in str(body)


def test_v1_cleanup_delete_safe(monkeypatch):
    client = _client()

    async def fake_delete(*_args, **_kwargs):
        return {
            "success": True,
            "message": "Deleted 1 item",
            "deleted_count": 1,
            "freed_bytes_estimate": 128,
            "failed_items": [],
        }

    monkeypatch.setattr(
        "api.routes.v1.cleanup.legacy.delete_server_cleanup_items",
        fake_delete,
    )
    response = client.post(
        "/api/v1/servers/7/cleanup/delete",
        json={"mode": "safe", "paths": []},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["deleted_count"] == 1
