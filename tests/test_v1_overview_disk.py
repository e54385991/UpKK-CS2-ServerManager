"""Coverage for cached Steam version and disk-space list endpoints."""

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


def _client(*, admin: bool = False):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="ops", is_admin=admin, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app)


def test_v1_steam_version_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/overview/steam-version")
    assert response.status_code == 401


def test_v1_steam_version_returns_cached_payload(monkeypatch):
    monkeypatch.setattr(
        "api.routes.v1.overview.a2s_cache_service.get_latest_steam_version",
        AsyncMock(
            return_value={
                "version": "1.41.2.3",
                "message": "ok",
                "timestamp": "2026-08-29T16:00:00+08:00",
            }
        ),
    )
    response = _client().get("/api/v1/overview/steam-version")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["version"] == "1.41.2.3"
    assert body["timestamp"]


def test_v1_disk_space_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/overview/disk-space")
    assert response.status_code == 401


def test_v1_disk_space_is_cache_only_by_default(monkeypatch):
    seen: list[str] = []

    async def fake_servers(_db, _user_id, skip=0, limit=1000):
        return [SimpleNamespace(id=7, game_directory="/home/steam/cs2")]

    async def fake_cached(keys):
        seen.extend(keys)
        return [{"used_gb": 12.5, "total_gb": 100.0, "available_gb": 80.0, "used_percent": 12.5}]

    async def unexpected_probe(*_args, **_kwargs):
        raise AssertionError("default batch read must not connect to SSH")

    monkeypatch.setattr("api.routes.v1.overview.Server.get_all_by_user", fake_servers)
    monkeypatch.setattr(
        "services.disk_space_service.redis_manager.get_many",
        fake_cached,
    )
    monkeypatch.setattr(
        "services.disk_space_service.disk_space_service._read_disk_space", unexpected_probe
    )
    response = _client().get("/api/v1/overview/disk-space")
    assert response.status_code == 200
    body = response.json()
    assert seen == ["disk_space:7"]
    assert body["servers"][0]["server_id"] == 7
    assert body["servers"][0]["cached"] is True
    assert body["servers"][0]["used_gb"] == 12.5


def test_v1_disk_space_scope_all_forbidden_for_non_admin():
    response = _client(admin=False).get("/api/v1/overview/disk-space?scope=all")
    assert response.status_code == 403


def test_v1_disk_space_scope_all_uses_fleet_for_admin(monkeypatch):
    async def fake_all(_db, skip=0, limit=1000):
        return [SimpleNamespace(id=2, game_directory="/tmp/cs2")]

    async def fake_mine(*_args, **_kwargs):
        raise AssertionError("admin fleet must not fall back to get_all_by_user")

    monkeypatch.setattr("api.routes.v1.overview.Server.get_all", fake_all)
    monkeypatch.setattr("api.routes.v1.overview.Server.get_all_by_user", fake_mine)
    monkeypatch.setattr(
        "services.disk_space_service.redis_manager.get_many",
        AsyncMock(return_value=[None]),
    )
    response = _client(admin=True).get("/api/v1/overview/disk-space?scope=all")
    assert response.status_code == 200
    assert response.json()["servers"][0]["server_id"] == 2
    assert response.json()["servers"][0]["cached"] is False


def test_v1_server_disk_space_is_cache_only_by_default(monkeypatch):
    seen: list[dict] = []

    async def fake_access(_db, server_id, _user):
        return SimpleNamespace(id=server_id, game_directory="/home/steam/cs2")

    async def fake_disk(server, force_refresh=False, cache_only=False):
        seen.append({"force_refresh": force_refresh, "cache_only": cache_only})
        return True, {"used_gb": 4.0, "total_gb": 40.0, "available_gb": 30.0, "used_percent": 10.0}

    monkeypatch.setattr("api.routes.v1.servers.require_server_access", fake_access)
    monkeypatch.setattr("api.routes.v1.servers.disk_space_service.get_disk_space", fake_disk)
    response = _client().get("/api/v1/servers/7/disk-space")
    assert response.status_code == 200
    assert seen == [{"force_refresh": False, "cache_only": True}]
    assert response.json()["cached"] is True
    assert response.json()["used_gb"] == 4.0


def test_v1_a2s_cache_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/overview/a2s-cache")
    assert response.status_code == 401


def test_v1_a2s_cache_is_cache_only_by_default(monkeypatch):
    seen: list[str] = []

    async def fake_servers(_db, _user_id, skip=0, limit=1000):
        return [SimpleNamespace(id=7, host="10.0.0.7", game_port=27015)]

    async def fake_cached(keys):
        seen.extend(keys)
        return [
            {
                "success": True,
                "server_info": {
                    "server_name": "ops",
                    "map_name": "de_dust2",
                    "player_count": 3,
                    "max_players": 10,
                    "version": "1.41.2.3",
                },
                "last_updated": "2026-08-29T16:00:00+08:00",
                "response_time_ms": 12,
            }
        ]

    async def fake_refresh(_server):
        raise AssertionError("default A2S list must not query live servers")

    monkeypatch.setattr("api.routes.v1.overview.Server.get_all_by_user", fake_servers)
    monkeypatch.setattr(
        "services.disk_space_service.redis_manager.get_many",
        fake_cached,
    )
    monkeypatch.setattr(
        "api.routes.v1.overview.a2s_cache_service.refresh_cached_info",
        fake_refresh,
    )
    response = _client().get("/api/v1/overview/a2s-cache")
    assert response.status_code == 200
    body = response.json()
    assert seen == ["a2s:server:7"]
    assert body["servers"][0]["server_id"] == 7
    assert body["servers"][0]["cached"] is True
    assert body["servers"][0]["player_count"] == 3
    assert body["servers"][0]["version"] == "1.41.2.3"


def test_v1_a2s_cache_scope_all_forbidden_for_non_admin():
    response = _client(admin=False).get("/api/v1/overview/a2s-cache?scope=all")
    assert response.status_code == 403


def test_v1_server_a2s_cache_is_cache_only_by_default(monkeypatch):
    seen: list[int] = []

    async def fake_access(_db, server_id, _user):
        return SimpleNamespace(id=server_id, host="10.0.0.7", game_port=27015)

    async def fake_cached(server_id):
        seen.append(server_id)
        return None

    async def fake_refresh(_server):
        raise AssertionError("default A2S read must not query live servers")

    monkeypatch.setattr("api.routes.v1.servers.require_server_access", fake_access)
    monkeypatch.setattr(
        "api.routes.v1.servers.a2s_cache_service.get_cached_info",
        fake_cached,
    )
    monkeypatch.setattr(
        "api.routes.v1.servers.a2s_cache_service.refresh_cached_info",
        fake_refresh,
    )
    response = _client().get("/api/v1/servers/7/a2s-cache")
    assert response.status_code == 200
    assert seen == [7]
    assert response.json()["cached"] is False
    assert response.json()["player_count"] is None
