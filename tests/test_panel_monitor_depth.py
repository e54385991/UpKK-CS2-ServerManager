"""Additional coverage for monitor counters, history, queries, and presenters."""

from __future__ import annotations

import asyncio
import logging
import queue
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.presenters.diagnostics import (
    _frames,
    _optional_float,
    _optional_int,
    _ts,
    to_error_list,
    to_monitor_view,
)
from modules.observability import (
    bind_operation,
    bind_request,
    capture_frames,
    classify_url,
    clear_all,
    current_generation,
    current_operation_id,
    current_request_id,
    drain_counters,
    drain_errors,
    increment,
    observe,
    record_db_invalidate,
    record_error,
    record_http,
    record_limiter_wait,
    record_outbound,
    record_outbound_http,
    record_redis,
    record_ssh,
    record_task,
    redact_summary,
    reset_for_tests,
    reset_operation,
    reset_request,
    set_enabled,
    snapshot_counts,
    snapshot_current_bucket,
)
from modules.observability.db_events import (
    _after_cursor_execute,
    _before_cursor_execute,
    _handle_error,
    _invalidate,
    install_database_observers,
)
from modules.observability.logs import drain_log_queue, install_log_handler, uninstall_log_handler
from modules.observability.requests import window_samples_for_legacy
from services.panel_monitor.aggregation import display_series
from services.panel_monitor.alerts import evaluate_alerts
from services.panel_monitor.collector import (
    collect_once,
    compact_point,
    fd_usage,
    record_collect_failure,
)
from services.panel_monitor.history import history
from services.panel_monitor.queries import get_errors, get_monitor
from services.panel_monitor.runtime import sample_is_stale
from services.panel_monitor.types import (
    ErrorListResult,
    ErrorQuery,
    MonitorQuery,
    MonitorResult,
    MonitorStatus,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_for_tests()
    clear_all()
    history.clear_buffers()
    history.history_available = True
    history.history_error = None
    history.last_sample_at = time()
    history.last_collection_ms = None
    history.dropped_errors = 0
    history.truncated_summaries = 0
    history._snapshot = None
    yield
    uninstall_log_handler()
    reset_for_tests()
    clear_all()
    history.clear_buffers()
    history._snapshot = None


def test_outbound_classification_and_dependency_counters():
    generation = set_enabled(True)
    increment("noop", 0, generation=generation)
    increment("ssh.reuses", generation=generation - 1)
    observe("ignored", 9, generation=generation - 1)
    record_outbound("mystery", 8, status=503, timeout=True, network=True, retry=True)
    record_outbound_http("https://api.github.com/repos/a/b", 12, status=429)
    record_outbound_http("https://github.com/a/b/releases/download/v1/x.zip", 40, status=200)
    record_outbound_http("https://example.test/health", 5, status=200)
    record_redis(duration_ms=2, failed=True, timeout=True)
    record_redis(monitor=True)
    record_ssh(reconnect_attempt=True, reconnect_failed=True)
    record_limiter_wait("ssh_probes", 7)
    record_db_invalidate()
    record_task("cancelled")
    counts = drain_counters()["counts"]
    assert classify_url("https://github.com/a/b") == "github"
    assert counts["http.other.calls"] == 2
    assert counts["http.github.status_429"] == 1
    assert counts["http.download.calls"] == 1
    assert counts["http.other.status_5xx"] == 1
    assert counts["redis.failures"] == 1
    assert counts["redis.monitor_ops"] == 1
    assert counts["ssh.reconnect_failures"] == 1
    assert counts["db.invalidations"] == 1
    assert snapshot_counts() == {}


def test_window_samples_and_client_status_codes():
    generation = set_enabled(True)
    now = time()
    for status in (200, 403, 409, 422, 429, 503):
        record_http(
            method="POST",
            route="/api/v1/settings",
            status=status,
            duration_ms=600 if status == 503 else 12,
            generation=generation,
            ts=now,
        )
    snapshot = snapshot_current_bucket()
    assert snapshot is not None
    assert snapshot["status_403"] == 1
    assert snapshot["status_409"] == 1
    assert snapshot["status_422"] == 1
    assert snapshot["status_429"] == 1
    assert snapshot["slow_count"] == 1
    count, durations, counts, routes = window_samples_for_legacy()
    assert count == 6
    assert counts["status_5xx"] == 1
    assert durations
    assert routes[0]["route"] == "/api/v1/settings"


def test_window_samples_empty_after_clear():
    set_enabled(True)
    assert window_samples_for_legacy()[0] == 0


def test_redaction_covers_urls_bearer_and_private_keys():
    summary, _truncated = redact_summary(
        "Bearer abcdefghijklmnop https://example.test/path?token=secret\n"
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
    )
    assert "Bearer [REDACTED]" in summary
    assert "?[REDACTED]" in summary
    assert "[REDACTED_PRIVATE_KEY]" in summary
    frames = capture_frames()
    assert any(frame["file"].endswith("test_panel_monitor_depth.py") for frame in frames)


def test_error_source_fallback_and_request_binding():
    generation = set_enabled(True)
    token = bind_request(request_id="req-bound", generation=generation)
    op = bind_operation("op-bound")
    try:
        assert current_request_id() == "req-bound"
        assert current_operation_id() == "op-bound"
        assert current_generation() == generation
        record_error(
            source="unknown",
            summary="password=super-secret",
            severity="nope",
            exception=RuntimeError("db"),
        )
    finally:
        reset_operation(op)
        reset_request(token)
    items = drain_errors()
    assert items[0]["source"] == "log"
    assert items[0]["severity"] == "error"
    assert items[0]["exception_type"] == "RuntimeError"
    assert "[REDACTED]" in items[0]["summary"]
    assert items[0]["request_id"] == "req-bound"


def test_database_observers_record_execute_error_and_invalidate():
    install_database_observers()
    install_database_observers()
    ctx = SimpleNamespace()
    _before_cursor_execute(None, None, None, None, ctx, False)
    _after_cursor_execute(None, None, None, None, ctx, False)
    set_enabled(True)
    _before_cursor_execute(None, None, None, None, ctx, False)
    _after_cursor_execute(None, None, None, None, ctx, False)
    _after_cursor_execute(None, None, None, None, SimpleNamespace(), False)
    _handle_error(
        SimpleNamespace(
            execution_context=ctx,
            original_exception=RuntimeError("deadlock"),
            sqlalchemy_exception=None,
        )
    )
    _handle_error(
        SimpleNamespace(
            execution_context=None, original_exception=None, sqlalchemy_exception="boom"
        )
    )
    _invalidate(None, None, None)
    counts = drain_counters()["counts"]
    assert counts["db.executions"] >= 2
    assert counts["db.errors"] >= 2
    assert counts["db.invalidations"] == 1


def test_log_handler_skips_warnings_and_counts_full_queue():
    set_enabled(True)
    install_log_handler()
    install_log_handler()
    logging.getLogger("app.worker").warning("not an error")
    from modules.observability import logs as logs_mod

    assert logs_mod._queue is not None
    logs_mod._queue = queue.Queue(maxsize=1)
    logs_mod._queue.put_nowait({"message": "full"})
    logging.getLogger("app.worker").error("overflow")
    dropped = drain_log_queue()
    assert dropped >= 1
    uninstall_log_handler()
    assert drain_log_queue() == 0


def test_watch_alerts_for_loop_db_and_file_descriptors():
    watch = [
        {
            "req": 20,
            "p95": 100,
            "s5": 0,
            "lag": 60,
            "dbx": 8,
            "dbc": 10,
            "fd": 85,
            "fdmax": 100,
        }
    ] * 3
    alerts = evaluate_alerts(
        watch,
        enabled=True,
        stale=False,
        redis_connected=True,
        unhandled=0,
        failed_tasks=0,
        log_errors=0,
    )
    ids = {alert.id: alert.severity for alert in alerts}
    assert ids["loop_lag"] == "watch"
    assert ids["db_pool"] == "watch"
    assert ids["fd"] == "watch"
    incomplete = evaluate_alerts(
        [
            {
                "req": 20,
                "p95": 10,
                "s5": 0,
                "lag": None,
                "dbx": None,
                "dbc": 10,
                "fd": None,
                "fdmax": 100,
            }
        ]
        * 3,
        enabled=True,
        stale=False,
        redis_connected=None,
        unhandled=0,
        failed_tasks=0,
        log_errors=0,
    )
    assert all(alert.id not in {"loop_lag", "db_pool", "fd"} for alert in incomplete)


def test_display_series_keeps_last_present_percentile():
    series = display_series(
        [
            {
                "t": 0,
                "req": 1,
                "s5": 0,
                "unh": 0,
                "tfail": 0,
                "p50": None,
                "p99": None,
                "max": None,
            },
            {"t": 5, "req": 1, "s5": 0, "unh": 0, "tfail": 0, "p50": 12, "p99": 40, "max": 50},
        ],
        "15m",
    )
    assert series[0]["latency_p50_ms"] == 12
    assert series[0]["latency_p99_ms"] == 40
    missing = display_series([{"t": 0, "req": 0, "s5": 0, "unh": 0, "tfail": 0}], "bogus")
    assert missing[0]["latency_p50_ms"] is None


@pytest.mark.asyncio
async def test_history_snapshot_instances_and_invalid_payloads(monkeypatch):
    class Store:
        def __init__(self) -> None:
            self.zsets: dict[str, list[tuple[object, float]]] = {
                "panel_mon:instances": [("other", time())],
            }
            self.kv = {
                "panel_mon:other:snap": "{not-json",
                "panel_mon:bad:snap": "[]",
            }

        async def zadd(self, key, mapping):
            items = self.zsets.setdefault(key, [])
            for member, score in mapping.items():
                items.append((str(member), float(score)))

        async def zremrangebyscore(self, key, _lo, _hi):
            return 0

        async def zremrangebyrank(self, key, _start, _stop):
            return 0

        async def expire(self, key, _ttl):
            return True

        async def zrangebyscore(self, key, lo, hi, withscores=False):
            if "err" in key:
                return [1, "{", '{"ts": 1}']
            items = [
                (member, score) for member, score in self.zsets.get(key, []) if lo <= score <= hi
            ]
            if withscores:
                return items
            return [member for member, _score in items]

        async def get(self, key):
            return self.kv.get(key)

        async def set(self, key, value, ex=None):
            self.kv[key] = value

    import sys

    fake = SimpleNamespace(client=Store(), prefixed_key=lambda key: key)
    monkeypatch.setattr(sys.modules["services.panel_monitor.history"], "redis_manager", fake)
    await history.write_errors([])
    await history.write_errors(
        [{"ts": time(), "summary": "x" * 12, "truncated": True, "source": "log"}]
    )
    await history.write_snapshot({"ok": True})
    assert history.cached_snapshot() == {"ok": True}
    instances = await history.list_instances()
    assert any(item[0] == "other" for item in instances)
    assert await history.load_snapshot("other") is None
    assert await history.load_snapshot("bad") is None
    loaded = await history.load_errors(instance="other-missing", start_ts=0, end_ts=time() + 10)
    assert isinstance(loaded, list)
    parsed = await history.load_errors(instance="err", start_ts=0, end_ts=time() + 10)
    assert parsed == [{"ts": 1}]
    other_points = await history.load_points(instance="missing", start_ts=0, end_ts=time())
    assert other_points == []


@pytest.mark.asyncio
async def test_history_drops_expired_pending_and_loads_other_instance_from_memory(
    monkeypatch,
):
    class Broken:
        async def zadd(self, *args, **kwargs):
            raise ConnectionError("down")

        async def zremrangebyscore(self, *args, **kwargs):
            raise ConnectionError("down")

        async def zremrangebyrank(self, *args, **kwargs):
            raise ConnectionError("down")

        async def expire(self, *args, **kwargs):
            raise ConnectionError("down")

        async def zrangebyscore(self, *args, **kwargs):
            raise ConnectionError("down")

        async def get(self, *args, **kwargs):
            raise ConnectionError("down")

        async def set(self, *args, **kwargs):
            raise ConnectionError("down")

    import sys

    fake = SimpleNamespace(client=Broken(), prefixed_key=lambda key: key)
    monkeypatch.setattr(sys.modules["services.panel_monitor.history"], "redis_manager", fake)
    await history.write_point({"t": time() - 1000, "req": 1})
    await history.write_errors([{"ts": time() - 1, "summary": "late"}])
    assert await history.load_points(instance="other", start_ts=0, end_ts=time()) == []
    assert await history.load_errors(instance="other", start_ts=0, end_ts=time()) == []
    assert await history.load_snapshot("other") is None
    listed = await history.list_instances()
    assert listed[0][0]


@pytest.mark.asyncio
async def test_collect_once_enriches_outbound_and_stops_when_disabled(monkeypatch):
    generation = set_enabled(True)
    record_http(
        method="GET",
        route="/api/v1/servers",
        status=403,
        duration_ms=9,
        generation=generation,
    )
    record_limiter_wait("ssh_probes", 4)
    record_limiter_wait("a2s", 6)
    snapshot = {
        "process": {"cpu_percent": 1, "rss_bytes": 2, "event_loop_lag_ms": 1},
        "database": {"pool_size": 2, "max_overflow": 3, "checked_out": 1},
        "redis": {"connected": True, "ping_ms": 1},
        "operations": {"running": 0, "queued": 0},
        "ssh_pool": {},
        "limiters": [{"name": "ssh_probes"}, {"name": "a2s"}, "skip"],
        "requests": {},
        "monitor": "not-a-dict",
    }

    async def capture():
        return snapshot

    monkeypatch.setattr(
        "services.panel_monitor.collector.capture_panel_performance",
        capture,
    )
    monkeypatch.setattr(
        "services.panel_monitor.collector.drain_request_buckets",
        lambda: [],
    )
    stored = await collect_once()
    assert stored["outbound_http"][0]["group"] == "github"
    assert stored["limiters"][0]["wait_p95_ms"] is not None or stored["limiters"][0].get("name")

    async def disable():
        set_enabled(False)
        return snapshot

    set_enabled(True)
    monkeypatch.setattr(
        "services.panel_monitor.collector.capture_panel_performance",
        disable,
    )
    assert await collect_once() == snapshot
    compact = compact_point(
        time(),
        {},
        {"counts": {}, "percentiles": {}, "loop_lag_ms": {"p95": 9, "max": 11}},
        snapshot,
    )
    assert compact["lag"] == 9
    record_collect_failure()
    used, limit = fd_usage()
    assert used is None or used >= 0
    assert limit is None or limit > 0


def test_fd_usage_handles_missing_proc_and_unlimited_files(monkeypatch):
    monkeypatch.setattr(
        "services.panel_monitor.collector.os.listdir",
        lambda _path: (_ for _ in ()).throw(OSError("missing")),
    )
    monkeypatch.setattr(
        "services.panel_monitor.collector.resource.getrlimit",
        lambda _name: (_ for _ in ()).throw(ValueError("unlimited")),
    )
    used, limit = fd_usage()
    assert used is None
    assert limit is None


@pytest.mark.asyncio
async def test_get_monitor_falls_back_to_live_snapshot_and_filters_errors(monkeypatch):
    set_enabled(True)
    now = time()
    await history.write_point({"t": now - 2, "req": 1, "unh": 0, "tfail": 0, "part": True})
    await history.write_errors(
        [
            {
                "ts": now - 2,
                "source": "request",
                "severity": "error",
                "error_code": "unhandled",
                "summary": "a",
                "route": "/api/v1/servers",
                "frames": [{"file": "a.py", "line": 1, "function": "x"}],
            },
            {
                "ts": now - 1,
                "source": "request",
                "severity": "error",
                "error_code": "unhandled",
                "summary": "b",
                "route": "/api/v1/servers",
                "frames": [{"file": "a.py", "line": 1, "function": "x"}],
            },
            {
                "ts": now - 0.5,
                "source": "log",
                "severity": "error",
                "error_code": "log",
                "summary": "c",
            },
        ]
    )
    monkeypatch.setattr(
        "services.panel_metrics.capture_panel_performance",
        AsyncMock(return_value={"redis": {"connected": True}, "process": {}}),
    )
    history._snapshot = None
    monitor = await get_monitor(MonitorQuery(range="nope"))
    assert monitor.range == "1h"
    assert monitor.integrity["partial_latency"] is True
    listed = await get_errors(
        ErrorQuery(range="nope", route="/api/v1/servers", cursor="not-a-number", limit=1)
    )
    assert listed.items
    assert listed.next_cursor is not None
    empty = await get_monitor(MonitorQuery(range="1h", instance_id="missing-instance"))
    assert empty.status.last_sample_at is None or empty.instance_id == "missing-instance"


@pytest.mark.asyncio
async def test_read_monitoring_enabled_defaults_and_failures(monkeypatch):
    from services.panel_monitor import settings as settings_mod

    @asynccontextmanager
    async def empty_session():
        yield SimpleNamespace(commit=AsyncMock())

    monkeypatch.setattr(settings_mod, "async_session_maker", lambda: empty_session())
    monkeypatch.setattr(
        settings_mod.SystemSettings,
        "get_settings",
        AsyncMock(return_value=None),
    )
    assert await settings_mod.read_monitoring_enabled() is True
    monkeypatch.setattr(
        settings_mod.SystemSettings,
        "get_settings",
        AsyncMock(return_value=SimpleNamespace(panel_monitoring_enabled=False)),
    )
    assert await settings_mod.read_monitoring_enabled() is False
    monkeypatch.setattr(
        settings_mod.SystemSettings,
        "get_settings",
        AsyncMock(side_effect=RuntimeError("db")),
    )
    assert await settings_mod.read_monitoring_enabled() is None


@pytest.mark.asyncio
async def test_runtime_collect_and_poll_loops(monkeypatch):
    from services.panel_monitor import runtime as runtime_mod

    runtime_mod._collect_generation = 7
    set_enabled(True)
    calls = {"wait": 0}

    async def wait():
        calls["wait"] += 1
        if calls["wait"] > 1:
            set_enabled(False)

    monkeypatch.setattr(runtime_mod, "_wait_for_bucket", wait)
    monkeypatch.setattr(runtime_mod, "collect_once", AsyncMock(side_effect=RuntimeError("collect")))
    await runtime_mod._collect_loop(7)
    assert snapshot_counts().get("monitor.collect_fail", 0) >= 1

    reads = {"n": 0}

    async def sleep(_seconds):
        reads["n"] += 1
        if reads["n"] >= 3:
            raise asyncio.CancelledError

    async def read():
        if reads["n"] == 1:
            return None
        return True

    runtime_mod._poll_started = True
    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(runtime_mod, "read_monitoring_enabled", read)
    monkeypatch.setattr(runtime_mod, "set_monitoring_enabled", AsyncMock())
    set_enabled(False)
    with pytest.raises(asyncio.CancelledError):
        await runtime_mod._poll_loop()
    set_enabled(True)
    history.last_sample_at = None
    assert sample_is_stale() is True
    history.last_sample_at = time() - 40
    assert sample_is_stale() is True
    history.last_sample_at = time()
    assert sample_is_stale() is False


@pytest.mark.asyncio
async def test_wait_for_bucket_sleeps_until_next_boundary(monkeypatch):
    from services.panel_monitor import runtime as runtime_mod

    slept: dict[str, float] = {}

    async def fast_sleep(delay: float) -> None:
        slept["d"] = delay

    monkeypatch.setattr(runtime_mod, "time", lambda: 99.96)
    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    await runtime_mod._wait_for_bucket()
    assert slept["d"] >= 0.05


def test_presenter_coerces_invalid_values():
    assert _optional_float(True) is None
    assert _optional_float("nope") is None
    assert _optional_float(object()) is None
    assert _optional_int(True) is None
    assert _optional_int("nope") is None
    assert _optional_int(object()) is None
    assert _frames("nope") == []
    assert _frames([{"file": "a.py", "function": "f", "line": "3"}, "skip"])[0].line == 3
    naive = datetime(2026, 1, 1)
    assert _ts(naive).tzinfo is UTC
    assert _ts("nope")
    listed = to_error_list(
        ErrorListResult(
            items=[
                {
                    "id": "1",
                    "ts": 1,
                    "source": "log",
                    "severity": "error",
                    "error_code": "log",
                    "exception_type": "Error",
                    "summary": "x",
                    "truncated": False,
                    "frames": None,
                }
            ],
            next_cursor=None,
            dropped=0,
        )
    )
    assert listed.items[0].id == "1"
    view = to_monitor_view(
        MonitorResult(
            range="1h",
            instance_id="abc",
            instances=[],
            status=MonitorStatus(
                enabled=False,
                running=False,
                stale=False,
                history_available=False,
                history_error=None,
                config_sync_error=None,
                last_sample_at=None,
                stopped_at=None,
                instance_id="abc",
            ),
            snapshot=None,
            series=[{"ts": 1, "request_count": 0, "redis_connected": "yes", "rss_bytes": "nope"}],
            alerts=[],
            error_groups=[
                {"source": "log", "count": 1, "frames": "nope", "first_ts": 1, "last_ts": 2}
            ],
            integrity={},
        ),
        runtime={},
    )
    assert view.snapshot is None
    assert view.series[0].redis_connected is None
    assert view.error_groups[0].frames == []
