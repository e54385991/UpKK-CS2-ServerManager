"""Coverage for the versioned ``/api/v1/servers/{id}/game-updates`` workspace."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db
from services.game_version import GameVersionStatus


def _database_session():
    return SimpleNamespace(
        add=lambda *_a, **_k: None,
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )


async def _fake_db():
    yield _database_session()


def _client(monkeypatch):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="owner", is_admin=False, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app), user


def _server(**overrides):
    values = {
        "id": 2,
        "name": "lan-ops",
        "current_game_version": "1.41.2.5",
        "enable_auto_update": True,
        "update_check_interval_hours": 1.0,
        "last_update_check": None,
        "last_update_time": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _snapshot(**overrides) -> GameVersionStatus:
    values = {
        "installed_version": "1.41.2.5",
        "installed_build_id": "14125",
        "installed_source": "steam.inf",
        "advertised_version": "1.41.2.6",
        "up_to_date": False,
        "steam_check_ok": True,
        "steam_message": "Server version required: 1.41.2.6",
        "steam_error": None,
    }
    values.update(overrides)
    return GameVersionStatus(**values)


def test_v1_game_updates_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/servers/2/game-updates")
    assert response.status_code == 401


def test_v1_game_updates_inspect_error_is_200(monkeypatch):
    client, _user = _client(monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.game_updates.require_server_access",
        AsyncMock(return_value=_server()),
    )
    monkeypatch.setattr(
        "api.routes.v1.game_updates.inspect_game_version",
        AsyncMock(side_effect=RuntimeError("steam blocked")),
    )

    listed = client.get("/api/v1/servers/2/game-updates")
    assert listed.status_code == 200
    body = listed.json()
    assert body["steam_check_ok"] is False
    assert body["installed_version"] == "1.41.2.5"
    assert body["advertised_version"] is None


def test_v1_game_updates_workspace(monkeypatch):
    client, _user = _client(monkeypatch)
    server = _server()
    monkeypatch.setattr(
        "api.routes.v1.game_updates.require_server_access",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(
        "api.routes.v1.game_updates.inspect_game_version",
        AsyncMock(return_value=_snapshot()),
    )

    listed = client.get("/api/v1/servers/2/game-updates")
    assert listed.status_code == 200
    body = listed.json()
    assert body["installed_version"] == "1.41.2.5"
    assert body["installed_build_id"] == "14125"
    assert body["installed_source"] == "steam.inf"
    assert body["advertised_version"] == "1.41.2.6"
    assert body["up_to_date"] is False
    assert body["enable_auto_update"] is True
    assert body["update_check_interval_hours"] == 1.0
    assert "password" not in body
    assert "token" not in body


def test_v1_game_updates_stringifies_numeric_build_id(monkeypatch):
    client, _user = _client(monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.game_updates.require_server_access",
        AsyncMock(return_value=_server()),
    )
    monkeypatch.setattr(
        "api.routes.v1.game_updates.inspect_game_version",
        AsyncMock(return_value=_snapshot(installed_build_id=2000897)),
    )

    listed = client.get("/api/v1/servers/2/game-updates")
    assert listed.status_code == 200
    assert listed.json()["installed_build_id"] == "2000897"


def test_v1_game_updates_put_settings(monkeypatch):
    client, _user = _client(monkeypatch)
    updated = _server(enable_auto_update=False, update_check_interval_hours=6.0)
    monkeypatch.setattr(
        "api.routes.v1.game_updates.update_legacy_server",
        AsyncMock(return_value=updated),
    )
    monkeypatch.setattr(
        "api.routes.v1.game_updates.inspect_game_version",
        AsyncMock(return_value=_snapshot(up_to_date=True, advertised_version="1.41.2.5")),
    )

    response = client.put(
        "/api/v1/servers/2/game-updates",
        json={"enable_auto_update": False, "update_check_interval_hours": 6},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enable_auto_update"] is False
    assert body["update_check_interval_hours"] == 6.0


def test_v1_game_updates_start_update_returns_202(monkeypatch):
    client, user = _client(monkeypatch)
    operation_id = str(uuid4())
    monkeypatch.setattr(
        "api.routes.v1.game_updates.require_server_access",
        AsyncMock(return_value=_server()),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.require_server_access",
        AsyncMock(return_value=_server()),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.enqueue_server_operation",
        AsyncMock(
            return_value={
                "operation_id": operation_id,
                "server_id": 2,
                "action": "update",
                "status": "queued",
                "success": None,
                "message": None,
                "server_status": None,
                "actor_user_id": user.id,
                "started_at": "2026-08-29T00:00:00+00:00",
                "completed_at": None,
            }
        ),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.redis_manager.get",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.maintenance_lock_service.is_locked",
        AsyncMock(return_value=False),
    )

    response = client.post(
        "/api/v1/servers/2/game-updates/operations",
        json={"action": "update"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["operation_id"] == operation_id
    assert body["action"] == "update"
    assert body["status"] == "queued"
    assert body["stream_url"] == f"/api/v1/servers/2/operations/{operation_id}/events"


def test_v1_game_updates_rejects_unknown_action(monkeypatch):
    client, _user = _client(monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.game_updates.require_server_access",
        AsyncMock(return_value=_server()),
    )
    response = client.post(
        "/api/v1/servers/2/game-updates/operations",
        json={"action": "deploy"},
    )
    assert response.status_code == 422


def test_v1_game_updates_releases_stale_lock_and_queues(monkeypatch):
    client, user = _client(monkeypatch)
    operation_id = str(uuid4())
    monkeypatch.setattr(
        "api.routes.v1.game_updates.require_server_access",
        AsyncMock(return_value=_server()),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.require_server_access",
        AsyncMock(return_value=_server()),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.server_operation_hub.get_current",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.redis_manager.get",
        AsyncMock(return_value="held"),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.redis_manager.delete",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.maintenance_lock_service.is_locked",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.enqueue_server_operation",
        AsyncMock(
            return_value={
                "operation_id": operation_id,
                "server_id": 2,
                "action": "validate",
                "status": "queued",
                "success": None,
                "message": None,
                "server_status": None,
                "actor_user_id": user.id,
                "started_at": "2026-08-29T00:00:00+00:00",
                "completed_at": None,
            }
        ),
    )

    response = client.post(
        "/api/v1/servers/2/game-updates/operations",
        json={"action": "validate"},
    )
    assert response.status_code == 202
    assert response.json()["operation_id"] == operation_id
