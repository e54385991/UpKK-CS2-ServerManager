"""I/O-free observability counters, buckets, redaction, and percentiles."""

from __future__ import annotations

import pytest

from modules.observability import (
    begin_request,
    clear_all,
    drain_counters,
    drain_request_buckets,
    end_request,
    error_stats,
    in_flight,
    nearest_rank,
    record_error,
    record_http,
    record_outbound,
    record_redis,
    record_task,
    record_unhandled,
    reset_for_tests,
    set_enabled,
)
from modules.observability.events import list_errors
from modules.observability.redact import redact_summary
from modules.observability.requests import snapshot_current_bucket
from modules.observability.stats import percentile_block


@pytest.fixture(autouse=True)
def _reset_observability():
    reset_for_tests()
    clear_all()
    yield
    reset_for_tests()
    clear_all()


def test_nearest_rank_matches_the_panel_metrics_window():
    assert nearest_rank([], 95) == 0.0
    assert nearest_rank([10.0], 95) == 10.0
    values = [float(item) for item in range(1, 101)]
    assert nearest_rank(values, 50) == 50.0
    assert nearest_rank(values, 95) == 95.0
    assert percentile_block(values)["max"] == 100.0
    assert percentile_block([]) == {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}


def test_disabled_switch_drops_samples_and_errors():
    record_http(method="GET", route="/x", status=200, duration_ms=12, generation=1)
    record_error(source="request", summary="should not store")
    record_task("failed")
    assert drain_request_buckets() == []
    assert drain_counters()["counts"] == {}
    items, dropped = list_errors(start_ts=0, end_ts=10**12)
    assert items == []
    assert dropped == 0


def test_http_buckets_count_status_and_unhandled():
    generation = set_enabled(True)
    record_http(
        method="GET",
        route="/api/v1/ssh-pool",
        status=200,
        duration_ms=11,
        generation=generation,
    )
    record_http(
        method="GET",
        route="/api/v1/servers",
        status=500,
        duration_ms=40,
        generation=generation,
    )
    record_unhandled(generation=generation)
    snapshot = snapshot_current_bucket()
    assert snapshot is not None
    assert snapshot["requests"] == 3
    assert snapshot["status_5xx"] == 2
    assert snapshot["unhandled"] == 1
    assert drain_request_buckets() == []


def test_in_flight_tracks_begin_and_end():
    set_enabled(True)
    begin_request()
    begin_request()
    assert in_flight() == 2
    end_request()
    assert in_flight() == 1
    end_request()
    end_request()
    assert in_flight() == 0


def test_redaction_truncates_and_strips_secrets():
    summary, truncated = redact_summary("token=github_pat_abcdefghijklmnopqrstuvwxyz")
    assert "github_pat_" not in summary
    assert "[REDACTED]" in summary
    long_summary, truncated = redact_summary("x" * 600)
    assert truncated is True
    assert len(long_summary) == 512


def test_stale_generation_does_not_record_after_toggle():
    first = set_enabled(True)
    set_enabled(False)
    second = set_enabled(True)
    assert first != second
    record_http(method="GET", route="/x", status=200, duration_ms=5, generation=first)
    snapshot = snapshot_current_bucket()
    assert snapshot is not None
    assert snapshot["requests"] == 0
    record_outbound("github", 12.0, status=200)
    record_redis(duration_ms=3.0)
    counts = drain_counters()["counts"]
    assert counts.get("http.github.calls") == 1
    assert counts.get("redis.ops") == 1


def test_latency_sample_cap_marks_partial_percentiles():
    generation = set_enabled(True)
    for index in range(4097):
        record_http(
            method="GET",
            route="/api/v1/ssh-pool",
            status=200,
            duration_ms=10.0 + index % 5,
            generation=generation,
        )
    snapshot = snapshot_current_bucket()
    assert snapshot is not None
    assert snapshot["requests"] == 4097
    assert snapshot["latency_partial"] is True
    assert snapshot["samples_dropped"] == 1


def test_list_errors_filters_source_severity_and_cursor():
    set_enabled(True)
    record_error(source="request", summary="req-a", severity="error", route="/api/v1/servers")
    record_error(source="task", summary="task-b", severity="critical", operation_id="op-1")
    record_error(source="request", summary="req-c", severity="error", route="/api/v1/servers")
    by_source, dropped = list_errors(start_ts=0, end_ts=10**12, source="request")
    assert dropped == 0
    assert {item["summary"] for item in by_source} == {"req-a", "req-c"}
    by_severity, _ = list_errors(start_ts=0, end_ts=10**12, severity="critical")
    assert [item["summary"] for item in by_severity] == ["task-b"]
    by_operation, _ = list_errors(start_ts=0, end_ts=10**12, operation_id="op-1")
    assert [item["summary"] for item in by_operation] == ["task-b"]
    newest = max(item["ts"] for item in by_source)
    older, _ = list_errors(start_ts=0, end_ts=10**12, source="request", cursor_ts=newest, limit=10)
    assert all(item["ts"] < newest for item in older)
    assert any(item["summary"] == "req-a" for item in older)


def test_record_error_ignores_stale_generation_and_counts_overflow(monkeypatch):
    from modules.observability import events as events_mod

    generation = set_enabled(True)
    record_error(source="request", summary="stale", generation=generation + 99)
    items, dropped = list_errors(start_ts=0, end_ts=10**12)
    assert items == []
    assert dropped == 0
    monkeypatch.setattr(events_mod, "MEMORY_ERROR_LIMIT", 1)
    record_error(source="request", summary="one")
    record_error(source="request", summary="x" * 600)
    stats = error_stats()
    assert stats["stored"] >= 1
    assert stats["dropped"] >= 1
    assert stats["truncated"] >= 1
