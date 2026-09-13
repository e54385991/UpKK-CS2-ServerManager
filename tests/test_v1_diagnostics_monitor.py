"""Admin diagnostics monitor and error-list routes."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.application import create_app
from modules import (
    get_current_active_user,
    get_current_admin_user,
    get_current_user,
    get_db,
)
from modules.observability import reset_for_tests, set_enabled
from services.panel_metrics import PRIORITY_SECTIONS
from services.panel_monitor.history import history
from services.panel_monitor.types import (
    ErrorListResult,
    ErrorQuery,
    MonitorAlert,
    MonitorInstance,
    MonitorResult,
    MonitorStatus,
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


def _result(*, enabled: bool = True) -> MonitorResult:
    return MonitorResult(
        range="1h",
        instance_id="1-abcd1234",
        instances=[MonitorInstance(instance_id="1-abcd1234", last_seen_ts=1.0, current=True)],
        status=MonitorStatus(
            enabled=enabled,
            running=enabled,
            stale=False,
            history_available=True,
            history_error=None,
            config_sync_error=None,
            last_sample_at=1.0,
            stopped_at=None,
            instance_id="1-abcd1234",
        ),
        snapshot=None,
        series=[
            {
                "ts": 1.0,
                "request_count": 20,
                "requests_per_minute": 120,
                "status_5xx": 1,
                "error_rate": 0.05,
                "latency_p95_ms": 40,
                "latency_partial": False,
                "cpu_percent": 3.2,
                "rss_bytes": 80_000_000,
                "queue_running": 0,
                "queue_queued": 1,
                "failed_tasks": 0,
                "unhandled_exceptions": 0,
            }
        ],
        alerts=[
            MonitorAlert(
                id="http_5xx",
                severity="watch",
                metric="http_5xx",
                title="5xx responses observed",
                detail="1 5xx response in the latest sample.",
                guidance="Check whether they cluster on one route.",
                threshold="any 5xx",
                value=1.0,
            )
        ]
        if enabled
        else [],
        error_groups=[],
        integrity={
            "sample_interval_seconds": 10,
            "display_bucket_seconds": 30,
            "point_count": 1,
            "gap": False,
            "partial_latency": False,
        },
    )


def _cached_snapshot() -> dict:
    return {
        "format": "upkk-panel-performance",
        "version": 1,
        "captured_at": datetime(2026, 9, 13, tzinfo=UTC),
        "priority": list(PRIORITY_SECTIONS),
        "requests": {
            "window_seconds": 60,
            "sample_count": 0,
            "latency_ms": {"p50": 0, "p95": 0, "p99": 0, "max": 0},
        },
        "process": {"pid": 1, "uptime_seconds": 12},
        "database": {"pool_size": 5, "max_overflow": 10, "capacity": 15},
        "redis": {"connected": True},
        "ssh_pool": {},
        "operations": {"queued": 0, "running": 0},
        "runtime": {
            "version": "0",
            "python": "3",
            "fastapi": "0",
            "git_sha": "unknown",
            "build_time": "unknown",
            "worker_pid": 1,
        },
    }


def test_monitor_requires_admin():
    client = _client(admin=False)
    assert client.get("/api/v1/diagnostics/monitor").status_code == 403
    assert client.get("/api/v1/diagnostics/errors").status_code == 403


def test_monitor_returns_status_series_and_alerts(monkeypatch):
    monkeypatch.setattr(
        "api.routes.v1.diagnostics.get_monitor",
        AsyncMock(return_value=_result()),
    )
    client = _client()
    response = client.get("/api/v1/diagnostics/monitor?range=1h")
    assert response.status_code == 200
    body = response.json()
    assert body["range"] == "1h"
    assert body["status"]["enabled"] is True
    assert body["series"][0]["request_count"] == 20
    assert body["alerts"][0]["severity"] == "watch"
    assert "password" not in str(body)


def test_monitor_disabled_keeps_history_without_current_alerts(monkeypatch):
    monkeypatch.setattr(
        "api.routes.v1.diagnostics.get_monitor",
        AsyncMock(return_value=_result(enabled=False)),
    )
    client = _client()
    body = client.get("/api/v1/diagnostics/monitor").json()
    assert body["status"]["enabled"] is False
    assert body["alerts"] == []
    assert body["series"][0]["request_count"] == 20


def test_errors_list_is_paginated(monkeypatch):
    monkeypatch.setattr(
        "api.routes.v1.diagnostics.get_errors",
        AsyncMock(
            return_value=ErrorListResult(
                items=[
                    {
                        "id": "abc",
                        "ts": 12.0,
                        "source": "request",
                        "severity": "error",
                        "error_code": "unhandled",
                        "exception_type": "RuntimeError",
                        "summary": "boom",
                        "truncated": False,
                        "route": "/api/v1/servers",
                        "operation_id": None,
                        "request_id": "req-1",
                        "frames": [
                            {"file": "api/routes/v1/servers.py", "function": "read", "line": 10}
                        ],
                    }
                ],
                next_cursor="11.0",
                dropped=2,
                truncated=False,
            )
        ),
    )
    client = _client()
    body = client.get("/api/v1/diagnostics/errors?limit=50").json()
    assert body["items"][0]["request_id"] == "req-1"
    assert body["next_cursor"] == "11.0"
    assert body["dropped"] == 2


def test_toggle_off_does_not_start_legacy_capture(monkeypatch):
    reset_for_tests()
    history.clear_buffers()
    set_enabled(False)
    client = _client()
    response = client.get("/api/v1/diagnostics")
    assert response.status_code == 409


def test_diagnostics_returns_cached_snapshot_when_disabled():
    reset_for_tests()
    set_enabled(False)
    history.remember_snapshot(_cached_snapshot())
    try:
        client = _client()
        response = client.get("/api/v1/diagnostics")
        assert response.status_code == 200
        body = response.json()
        assert body["format"] == "upkk-panel-performance"
        assert body["requests"]["sample_count"] == 0
        assert body["runtime"]["worker_pid"]
    finally:
        history._snapshot = None


def test_errors_forwards_filters(monkeypatch):
    captured: dict[str, object] = {}

    async def fake(query):
        captured["query"] = query
        return ErrorListResult(items=[], next_cursor=None, dropped=0, truncated=True)

    monkeypatch.setattr("api.routes.v1.diagnostics.get_errors", fake)
    client = _client()
    response = client.get(
        "/api/v1/diagnostics/errors"
        "?range=15m&source=request&severity=error&route=/api/v1/servers"
        "&operation_id=op-1&cursor=12.5&limit=10"
    )
    assert response.status_code == 200
    query = captured["query"]
    assert isinstance(query, ErrorQuery)
    assert query.range == "15m"
    assert query.source == "request"
    assert query.severity == "error"
    assert query.route == "/api/v1/servers"
    assert query.operation_id == "op-1"
    assert query.cursor == "12.5"
    assert query.limit == 10
    assert response.json()["truncated"] is True
