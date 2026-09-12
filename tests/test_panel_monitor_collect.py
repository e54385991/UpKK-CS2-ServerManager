"""Collector, runtime, query, and extra observability coverage for panel monitoring."""

from __future__ import annotations

import logging
from time import time
from unittest.mock import AsyncMock

import pytest

from modules.observability import (
    capture_frames,
    clear_all,
    drain_counters,
    drain_errors,
    error_stats,
    instance_id,
    is_enabled,
    record_cancellation,
    record_db_execute,
    record_error,
    record_http,
    record_loop_lag,
    record_ssh,
    record_stream_error,
    record_task,
    reset_for_tests,
    set_enabled,
    snapshot_current_bucket,
    sync_error,
)
from modules.observability.logs import drain_log_queue, install_log_handler, uninstall_log_handler
from services.panel_monitor.alerts import evaluate_alerts
from services.panel_monitor.collector import collect_once, compact_point, fd_usage
from services.panel_monitor.history import history
from services.panel_monitor.queries import get_errors, get_monitor
from services.panel_monitor.types import ErrorQuery, MonitorQuery


@pytest.fixture(autouse=True)
def _reset():
    reset_for_tests()
    clear_all()
    history.clear_buffers()
    history.history_available = True
    history.history_error = None
    history.last_sample_at = time()
    history._snapshot = None
    yield
    uninstall_log_handler()
    reset_for_tests()
    clear_all()
    history.clear_buffers()
    history._snapshot = None


def _snapshot() -> dict:
    return {
        "process": {"cpu_percent": 4.5, "rss_bytes": 12_000, "event_loop_lag_ms": 3.0},
        "database": {"pool_size": 5, "max_overflow": 10, "checked_out": 2},
        "redis": {"connected": True, "ping_ms": 1.2},
        "operations": {"running": 1, "queued": 2, "oldest_queue_ms": 40},
        "ssh_pool": {},
        "limiters": [{"name": "ssh_probes"}],
        "requests": {},
    }


def test_capture_frames_keeps_project_paths():
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        frames = capture_frames(exc)
    assert frames
    assert frames[0]["file"].endswith("test_panel_monitor_collect.py")
    assert frames[0]["function"] == "test_capture_frames_keeps_project_paths"


def test_status_codes_sse_and_dependency_counters():
    generation = set_enabled(True)
    record_http(
        method="GET",
        route="/login",
        status=401,
        duration_ms=8,
        generation=generation,
    )
    record_http(
        method="GET",
        route="/api/v1/servers",
        status=404,
        duration_ms=9,
        generation=generation,
    )
    record_http(
        method="GET",
        route="/ops-stream/x",
        status=200,
        duration_ms=30,
        generation=generation,
        sse=True,
        first_byte_ms=4,
    )
    record_cancellation(generation=generation)
    record_stream_error(generation=generation)
    record_db_execute(250, error=True)
    record_loop_lag(12)
    record_ssh(reused=True, connect_ms=15, auth_failed=True, timeout=True)
    record_task("failed", queue_ms=11, execute_ms=20)
    snapshot = snapshot_current_bucket()
    assert snapshot is not None
    assert snapshot["status_401"] == 1
    assert snapshot["status_404"] == 1
    assert snapshot["sse_count"] == 1
    assert snapshot["cancellations"] == 1
    assert snapshot["stream_errors"] == 1
    counts = drain_counters()["counts"]
    assert counts["db.errors"] == 1
    assert counts["db.slow"] == 1
    assert counts["ssh.reuses"] == 1
    assert counts["task.failed"] == 1


def test_log_handler_records_errors_and_skips_monitor_loggers():
    set_enabled(True)
    install_log_handler()
    logging.getLogger("services.panel_monitor.collector").error("should skip")
    logging.getLogger("tests.panel-monitor").error("visible failure")
    drain_log_queue()
    items = drain_errors()
    assert any(item["summary"] == "visible failure" for item in items)
    assert all("should skip" not in item["summary"] for item in items)
    uninstall_log_handler()


def test_alerts_cover_resources_failures_and_stale_data():
    critical = [
        {
            "req": 25,
            "p95": 1200,
            "s5": 4,
            "lag": 220,
            "dbx": 10,
            "dbc": 10,
            "fd": 98,
            "fdmax": 100,
        }
    ] * 3
    alerts = evaluate_alerts(
        critical,
        enabled=True,
        stale=True,
        redis_connected=False,
        unhandled=2,
        failed_tasks=1,
        log_errors=3,
    )
    ids = {alert.id for alert in alerts}
    assert {
        "stale",
        "request_p95",
        "http_5xx",
        "loop_lag",
        "db_pool",
        "fd",
        "redis",
        "unhandled",
        "failed_tasks",
        "log_errors",
    } <= ids
    assert any(alert.id == "request_p95" and alert.severity == "critical" for alert in alerts)
    assert any(alert.id == "http_5xx" and alert.severity == "critical" for alert in alerts)


@pytest.mark.asyncio
async def test_collect_once_persists_compact_history(monkeypatch):
    generation = set_enabled(True)
    record_http(
        method="GET",
        route="/api/v1/servers",
        status=200,
        duration_ms=18,
        generation=generation,
    )
    record_error(source="request", summary="fixture boom", error_code="unhandled")
    monkeypatch.setattr(
        "services.panel_monitor.collector.capture_panel_performance",
        AsyncMock(return_value=_snapshot()),
    )
    monkeypatch.setattr(
        "services.panel_monitor.collector.drain_request_buckets",
        lambda: [
            {
                "start_ts": time() - 10,
                "requests": 4,
                "status_5xx": 0,
                "unhandled": 0,
                "latency_ms": {"p95": 20, "p50": 10, "p99": 30, "max": 40},
                "latency_partial": False,
            }
        ],
    )
    snapshot = await collect_once()
    assert snapshot["database"]["capacity"] == 15
    assert snapshot["monitor"]["collection_ms"] is not None
    point = compact_point(
        time(),
        {"requests": 2, "status_5xx": 0, "latency_ms": {"p95": 20}},
        drain_counters(),
        snapshot,
    )
    assert point["dbc"] == 15
    loaded = await history.load_points(instance=instance_id(), start_ts=0, end_ts=time() + 10)
    assert loaded
    open_fds, limit = fd_usage()
    assert open_fds is None or open_fds >= 0
    assert limit is None or limit > 0


@pytest.mark.asyncio
async def test_get_monitor_and_errors_use_history_and_filters():
    set_enabled(True)
    now = time()
    await history.write_point(
        {
            "t": now - 5,
            "req": 20,
            "s5": 1,
            "unh": 1,
            "tfail": 1,
            "p95": 40,
            "part": False,
        }
    )
    await history.write_errors(
        [
            {
                "ts": now - 4,
                "source": "request",
                "severity": "error",
                "error_code": "unhandled",
                "summary": "boom",
                "route": "/api/v1/servers",
                "truncated": False,
                "frames": [{"file": "api/routes/v1/servers.py", "line": 9, "function": "read"}],
            },
            {
                "ts": now - 3,
                "source": "task",
                "severity": "error",
                "error_code": "failed",
                "summary": "job",
                "operation_id": "op-1",
            },
        ]
    )
    monitor = await get_monitor(MonitorQuery(range="1h"))
    assert monitor.error_groups
    assert monitor.integrity["point_count"] >= 1
    listed = await get_errors(ErrorQuery(range="1h", source="request", limit=1))
    assert listed.items
    assert listed.items[0]["source"] == "request"
    assert listed.next_cursor is None or listed.next_cursor


@pytest.mark.asyncio
async def test_runtime_starts_and_stops_without_leaving_enabled(monkeypatch):
    from services.panel_monitor import runtime as runtime_mod

    runtime_mod._poll_started = False
    runtime_mod._workers_running = False
    runtime_mod._collect_task = None
    monkeypatch.setattr(runtime_mod, "start_loop_sampler", AsyncMock())
    monkeypatch.setattr(runtime_mod, "stop_loop_sampler", AsyncMock())
    monkeypatch.setattr(runtime_mod, "collect_once", AsyncMock(return_value={}))
    monkeypatch.setattr(runtime_mod, "read_monitoring_enabled", AsyncMock(return_value=True))
    try:
        await runtime_mod.start_panel_monitor()
        assert is_enabled()
        await runtime_mod.set_monitoring_enabled(False)
        assert is_enabled() is False
        monkeypatch.setattr(runtime_mod, "read_monitoring_enabled", AsyncMock(return_value=None))
        await runtime_mod.start_panel_monitor()
        assert sync_error() == "config_read_failed"
        assert is_enabled() is False
    finally:
        await runtime_mod.stop_panel_monitor()


def test_error_stats_count_recorded_details():
    set_enabled(True)
    record_error(source="database", summary="db down", error_code="db_error")
    stats = error_stats()
    assert stats["stored"] >= 1
