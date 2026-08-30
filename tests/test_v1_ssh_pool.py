"""Coverage for the versioned SSH pool snapshot and overview counters."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db
from modules.models.servers import ServerStatus


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _client():
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="ops", is_admin=False, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app)


def _pool_stats() -> dict:
    return {
        "total_connections": 2,
        "alive_connections": 2,
        "in_use_connections": 1,
        "idle_connections": 1,
        "active_leases": 3,
        "draining_connections": 0,
        "idle_timeout": 900,
        "max_lifetime": 3600,
        "keepalive_interval": 30,
        "keepalive_count_max": 3,
    }


def test_v1_ssh_pool_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/ssh-pool")
    assert response.status_code == 401


def test_v1_ssh_pool_returns_live_counts(monkeypatch):
    monkeypatch.setattr(
        "api.routes.v1.ssh_pool.ssh_connection_pool.get_pool_stats",
        AsyncMock(return_value=_pool_stats()),
    )
    client = _client()
    response = client.get("/api/v1/ssh-pool")
    assert response.status_code == 200
    body = response.json()
    assert body["connections"] == 2
    assert body["in_use"] == 1
    assert body["idle"] == 1
    assert body["leases"] == 3
    assert body["keepalive_interval"] == 30
    assert "password" not in str(body)


def test_v1_overview_includes_ssh_pool_counts(monkeypatch):
    async def fake_servers(_db, _user_id, skip=0, limit=1000):
        return [
            SimpleNamespace(status=ServerStatus.RUNNING, max_players=16),
            SimpleNamespace(status=ServerStatus.ERROR, max_players=10),
        ]

    monkeypatch.setattr(
        "api.routes.v1.overview.Server.get_all_by_user",
        fake_servers,
    )
    monkeypatch.setattr(
        "api.routes.v1.ssh_pool.ssh_connection_pool.get_pool_stats",
        AsyncMock(return_value=_pool_stats()),
    )
    client = _client()
    response = client.get("/api/v1/overview/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["running"] == 1
    assert body["attention"] == 1
    assert body["capacity"] == 26
    assert body["ssh_connections"] == 2
    assert body["ssh_in_use"] == 1
    assert body["ssh_idle"] == 1
    assert body["ssh_leases"] == 3
