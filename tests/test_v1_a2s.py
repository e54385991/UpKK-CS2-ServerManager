"""Coverage for versioned A2S query and monitoring-log endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db


def _client():
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="ops", is_admin=True, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    async def fake_db():
        yield SimpleNamespace()

    app.dependency_overrides[get_db] = fake_db
    return TestClient(app)


def _server():
    return SimpleNamespace(
        id=7,
        host="10.0.0.8",
        game_port=27015,
        a2s_query_host="a2s.example",
        a2s_query_port=27016,
    )


def test_v1_a2s_default_read_uses_cache_only(monkeypatch):
    seen = []

    async def fake_access(_db, server_id, _user):
        assert server_id == 7
        return _server()

    async def fake_cached(server_id):
        seen.append(server_id)
        return {
            "query_host": "a2s.example",
            "query_port": 27016,
            "success": True,
            "server_info": {
                "server_name": "lan-ops",
                "map_name": "de_dust2",
                "player_count": 3,
                "max_players": 16,
                "bot_count": 0,
                "version": "1.40.0.0",
            },
            "players": [{"name": "alice", "score": 12, "duration": 90.5}],
            "timestamp": "2026-08-29T12:00:00+00:00",
            "last_updated": "2026-08-29T12:00:00+00:00",
            "response_time_ms": 42,
        }

    async def fake_refresh(_server):
        raise AssertionError("default A2S read must not query live servers")

    monkeypatch.setattr("api.routes.v1.servers.require_server_access", fake_access)
    monkeypatch.setattr("api.routes.v1.servers.a2s_cache_service.get_cached_info", fake_cached)
    monkeypatch.setattr(
        "api.routes.v1.servers.a2s_cache_service.refresh_cached_info",
        fake_refresh,
    )
    response = _client().get("/api/v1/servers/7/a2s")
    assert response.status_code == 200
    body = response.json()
    assert seen == [7]
    assert body["cached"] is True
    assert body["live"] is False
    assert body["success"] is True
    assert body["query_host"] == "a2s.example"
    assert body["query_port"] == 27016
    assert body["server_info"]["map_name"] == "de_dust2"
    assert body["players"][0]["name"] == "alice"
    assert body["response_time_ms"] == 42


def test_v1_a2s_live_query_refreshes_cache(monkeypatch):
    refreshed = []

    async def fake_access(_db, server_id, _user):
        return _server()

    async def fake_refresh(server):
        refreshed.append(server.id)
        return {
            "query_host": server.a2s_query_host,
            "query_port": server.a2s_query_port,
            "success": False,
            "error": "timeout",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    monkeypatch.setattr("api.routes.v1.servers.require_server_access", fake_access)
    monkeypatch.setattr(
        "api.routes.v1.servers.a2s_cache_service.refresh_cached_info",
        fake_refresh,
    )
    response = _client().get("/api/v1/servers/7/a2s?live=true")
    assert response.status_code == 200
    body = response.json()
    assert refreshed == [7]
    assert body["live"] is True
    assert body["success"] is False
    assert body["error"] == "timeout"


def test_v1_monitoring_logs_return_a2s_check_lines(monkeypatch):
    async def fake_access(_db, server_id, _user):
        return _server()

    async def fake_logs(server_id, event_type=None, limit=50):
        assert server_id == 7
        assert event_type == "a2s_check"
        assert limit == 50
        return [
            {
                "id": 123,
                "event_type": "a2s_check",
                "status": "success",
                "message": "A2S ok 3/16",
                "created_at": "2026-08-29T12:01:00",
            }
        ]

    monkeypatch.setattr("api.routes.v1.servers.require_server_access", fake_access)
    monkeypatch.setattr("api.routes.v1.servers.redis_manager.get_monitoring_logs", fake_logs)
    response = _client().get("/api/v1/servers/7/monitoring-logs?event_type=a2s_check")
    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["id"] == "123"
    assert items[0]["status"] == "success"
    assert items[0]["message"] == "A2S ok 3/16"
