"""Contract coverage for cached Linux host information on the overview."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db


def _database_session():
    return SimpleNamespace()


async def _fake_db():
    yield _database_session()


def _client(*, admin: bool = False):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="ops", is_admin=admin, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app)


def _host_info(server_id: int = 7) -> dict[str, object]:
    return {
        "server_id": server_id,
        "cached": True,
        "success": True,
        "system_type": "Linux",
        "architecture": "x86_64",
        "cpu_model": "Intel Xeon",
        "cpu_cores": 8,
        "kernel_version": "6.8.0",
        "distribution": "debian",
        "distribution_version": "12",
        "distribution_pretty_name": "Debian GNU/Linux 12 (bookworm)",
        "memory_total_bytes": 17179869184,
        "memory_available_bytes": 8589934592,
        "collected_at": "2026-09-04T13:00:00+08:00",
    }


def test_v1_host_system_info_requires_authentication():
    response = TestClient(create_app(lifespan=None)).get("/api/v1/overview/host-system-info")
    assert response.status_code == 401


def test_v1_host_system_info_returns_cached_server_snapshots(monkeypatch):
    server = SimpleNamespace(id=7)

    async def fake_servers(_db, _user_id, skip=0, limit=1000):
        return [server]

    service = AsyncMock(return_value=_host_info())
    monkeypatch.setattr("api.routes.v1.overview.Server.get_all_by_user", fake_servers)
    monkeypatch.setattr(
        "api.routes.v1.overview.host_system_info_service.get_host_system_info",
        service,
    )

    response = _client().get("/api/v1/overview/host-system-info")

    assert response.status_code == 200
    body = response.json()
    assert body["servers"][0]["distribution"] == "debian"
    assert body["servers"][0]["memory_available_bytes"] == 8589934592
    service.assert_awaited_once_with(server, force_refresh=False)


def test_v1_host_system_info_scope_all_forbidden_for_non_admin():
    response = _client().get("/api/v1/overview/host-system-info?scope=all")
    assert response.status_code == 403
