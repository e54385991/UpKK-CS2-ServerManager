"""Coverage for in-process panel performance samples and the admin snapshot."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.application import create_app
from modules import (
    get_current_active_user,
    get_current_admin_user,
    get_current_user,
    get_db,
)
from services.panel_metrics import (
    PRIORITY_SECTIONS,
    RequestSample,
    metrics_store,
    percentiles,
    request_metrics_payload,
    route_label,
    should_record_path,
)


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _client(*, admin: bool = True) -> TestClient:
    app = create_app(lifespan=None)
    user = SimpleNamespace(
        id=1, username="admin" if admin else "member", is_admin=admin, is_active=True
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    if admin:
        app.dependency_overrides[get_current_admin_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app)


def test_percentiles_use_nearest_rank_and_empty_zero():
    assert percentiles([]) == {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    assert percentiles([10.0]) == {"p50": 10.0, "p95": 10.0, "p99": 10.0, "max": 10.0}
    values = [float(item) for item in range(1, 101)]
    ranked = percentiles(values)
    assert ranked["p50"] == 50.0
    assert ranked["p95"] == 95.0
    assert ranked["p99"] == 99.0
    assert ranked["max"] == 100.0


def test_route_label_normalizes_ids_and_prefers_template():
    assert route_label("/api/v1/servers/12/plugins", None) == "/api/v1/servers/{id}/plugins"
    assert (
        route_label(
            "/api/v1/operations/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            None,
        )
        == "/api/v1/operations/{id}"
    )
    assert route_label("/ignored", "/api/v1/ssh-pool") == "/api/v1/ssh-pool"


def test_health_and_diagnostics_are_not_recorded():
    assert should_record_path("/health") is False
    assert should_record_path("/api/v1/diagnostics") is False
    assert should_record_path("/api/v1/diagnostics/monitor") is False
    assert should_record_path("/api/v1/diagnostics/errors") is False
    assert should_record_path("/api/v1/ssh-pool") is True


def test_slowest_routes_are_listed_first():
    samples = [
        RequestSample(ts=1, method="GET", route="/fast", status=200, duration_ms=10),
        RequestSample(ts=2, method="GET", route="/slow", status=200, duration_ms=800),
        RequestSample(ts=3, method="GET", route="/slow", status=500, duration_ms=900),
    ]
    payload = request_metrics_payload(samples)
    assert payload["sample_count"] == 3
    assert payload["status_5xx"] == 1
    assert payload["slow_request_count"] == 2
    routes = payload["by_route"]
    assert isinstance(routes, list)
    assert routes[0]["route"] == "/slow"
    assert routes[0]["error_count"] == 1


def test_v1_diagnostics_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/diagnostics")
    assert response.status_code == 401


def test_v1_diagnostics_rejects_non_admin():
    client = _client(admin=False)
    response = client.get("/api/v1/diagnostics")
    assert response.status_code == 403


def test_v1_diagnostics_returns_priority_snapshot(monkeypatch):
    from modules.observability import reset_for_tests, set_enabled

    reset_for_tests()
    set_enabled(True)
    metrics_store.clear()
    metrics_store.record_request(
        method="GET",
        route="/api/v1/ssh-pool",
        status=200,
        duration_ms=12.5,
    )
    monkeypatch.setattr(
        "services.panel_metrics.ssh_connection_pool.get_pool_stats",
        AsyncMock(
            return_value={
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
        ),
    )
    monkeypatch.setattr("services.panel_metrics.redis_manager.ping", AsyncMock(return_value=True))
    client = _client()
    client.get("/health")
    response = client.get("/api/v1/diagnostics")
    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "upkk-panel-performance"
    assert body["priority"] == list(PRIORITY_SECTIONS)
    assert body["requests"]["sample_count"] >= 1
    recorded_routes = [item["route"] for item in body["requests"]["by_route"]]
    assert "/health" not in recorded_routes
    assert "/api/v1/diagnostics" not in recorded_routes
    assert body["ssh_pool"]["in_use"] == 1
    assert body["redis"]["connected"] is True
    assert body["limiters"][0]["name"] == "ssh_probes"
    assert "password" not in str(body)
    assert "token" not in str(body)
    assert response.headers.get("server-timing", "").startswith("app;dur=")
    health = client.get("/health")
    assert health.status_code == 200
    assert health.headers.get("server-timing", "").startswith("app;dur=")


def test_v1_diagnostics_disabled_without_snapshot_is_conflict():
    from modules.observability import reset_for_tests

    reset_for_tests()
    from services.panel_monitor.history import history

    history.clear_buffers()
    client = _client()
    response = client.get("/api/v1/diagnostics")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "MONITORING_DISABLED"


@pytest.mark.asyncio
async def test_loop_sampler_starts_and_stops_cleanly():
    from services.panel_metrics import start_loop_sampler, stop_loop_sampler

    await start_loop_sampler()
    await start_loop_sampler()
    await stop_loop_sampler()
    await stop_loop_sampler()
