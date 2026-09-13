"""Panel monitor aggregation, alerts, and history fallback."""

from __future__ import annotations

from collections import deque
from time import time
from typing import Any

import pytest

from modules.observability import clear_all, instance_id, reset_for_tests, set_enabled
from services.panel_monitor.aggregation import display_series
from services.panel_monitor.alerts import evaluate_alerts
from services.panel_monitor.history import MEMORY_ERRORS, MEMORY_POINTS, history
from services.panel_monitor.queries import get_monitor
from services.panel_monitor.types import MonitorQuery
from tests.panel_monitor_fakes import FakeRedisClient, patch_history_redis


def _reset_history() -> None:
    history.history_available = True
    history.history_error = None
    history.last_sample_at = None
    history.dropped_errors = 0
    history.truncated_summaries = 0
    history._snapshot = None
    history._memory_points = deque(maxlen=MEMORY_POINTS)
    history._memory_errors = deque(maxlen=MEMORY_ERRORS)
    history._pending_points = deque(maxlen=MEMORY_POINTS)
    history._pending_errors = deque(maxlen=MEMORY_ERRORS)


def _alerts(
    points: list[dict[str, Any]],
    *,
    enabled: bool = True,
    stale: bool = False,
    redis_connected: bool | None = True,
    unhandled: int = 0,
    failed_tasks: int = 0,
    log_errors: int = 0,
):
    return evaluate_alerts(
        points,
        enabled=enabled,
        stale=stale,
        redis_connected=redis_connected,
        unhandled=unhandled,
        failed_tasks=failed_tasks,
        log_errors=log_errors,
    )


@pytest.fixture(autouse=True)
def _reset():
    reset_for_tests()
    clear_all()
    _reset_history()
    yield
    reset_for_tests()
    clear_all()
    _reset_history()


def test_display_series_peaks_p95_and_sums_requests():
    points = [
        {"t": 0, "req": 10, "s5": 0, "unh": 0, "tfail": 0, "p95": 100, "rss": 100, "cpu": 1},
        {"t": 10, "req": 10, "s5": 1, "unh": 0, "tfail": 2, "p95": 800, "rss": 120, "cpu": 2},
        {"t": 20, "req": 5, "s5": 0, "unh": 0, "tfail": 0, "p95": 200, "rss": 110, "cpu": 3},
    ]
    series = display_series(points, "1h")
    assert len(series) == 1
    assert series[0]["request_count"] == 25
    assert series[0]["status_5xx"] == 1
    assert series[0]["latency_p95_ms"] == 800
    assert series[0]["failed_tasks"] == 2
    assert series[0]["cpu_percent"] == 3
    assert series[0]["rss_peak_bytes"] == 120


def test_alerts_need_hysteresis_for_latency_and_fire_immediately_for_5xx():
    quiet = [{"req": 20, "p95": 100, "s5": 0, "lag": 10, "dbx": 1, "dbc": 10}]
    assert _alerts(quiet * 3) == []
    watch = _alerts([{"req": 20, "p95": 600, "s5": 0, "lag": 10, "dbx": 1, "dbc": 10}] * 3)
    assert any(alert.id == "request_p95" and alert.severity == "watch" for alert in watch)
    errors = _alerts([{"req": 4, "p95": 10, "s5": 1, "lag": 1, "dbx": 1, "dbc": 10}])
    assert any(alert.id == "http_5xx" and alert.severity == "watch" for alert in errors)
    disabled = _alerts(
        [{"req": 20, "p95": 600, "s5": 0, "lag": 10, "dbx": 1, "dbc": 10}] * 3,
        enabled=False,
        redis_connected=False,
        unhandled=3,
        failed_tasks=1,
        log_errors=4,
    )
    assert disabled == []


def test_empty_recent_and_mixed_fd_do_not_alert():
    assert _alerts([]) == []
    mixed = [
        {"req": 20, "p95": 10, "s5": 0, "fd": 90, "fdmax": 100},
        {"req": 20, "p95": 10, "s5": 0, "fd": 10, "fdmax": 100},
        {"req": 20, "p95": 10, "s5": 0, "fd": 10, "fdmax": 100},
    ]
    assert all(alert.id != "fd" for alert in _alerts(mixed))
    zero_capacity = [{"req": 20, "p95": 10, "s5": 0, "dbx": 1, "dbc": 0}] * 3
    assert all(alert.id != "db_pool" for alert in _alerts(zero_capacity))


@pytest.mark.asyncio
async def test_history_falls_back_to_memory_when_redis_fails(monkeypatch):
    patch_history_redis(monkeypatch, FakeRedisClient(fail=True))
    point = {"t": time(), "req": 3, "s5": 0}
    await history.write_point(point)
    assert history.history_available is False
    loaded = await history.load_points(instance=instance_id(), start_ts=0, end_ts=time() + 10)
    assert loaded
    assert loaded[-1]["req"] == 3


@pytest.mark.asyncio
async def test_get_monitor_does_not_claim_healthy_when_disabled():
    set_enabled(False)
    result = await get_monitor(MonitorQuery(range="1h"))
    assert result.status.enabled is False
    assert result.alerts == []
    assert result.status.collecting is False


@pytest.mark.asyncio
async def test_history_replays_pending_points_after_redis_recovers(monkeypatch):
    client = patch_history_redis(monkeypatch, FakeRedisClient(fail=True))
    await history.write_point({"t": time(), "req": 1})
    assert history.history_available is False
    client.fail = False
    await history.write_point({"t": time(), "req": 2})
    assert history.history_available is True
    loaded = await history.load_points(instance=instance_id(), start_ts=0, end_ts=time() + 10)
    assert {int(point["req"]) for point in loaded} >= {1, 2}


@pytest.mark.asyncio
async def test_history_drops_expired_pending_then_flushes_errors(monkeypatch):
    client = patch_history_redis(monkeypatch, FakeRedisClient(fail=True))
    await history.write_point({"t": time() - 1000, "req": 1})
    await history.write_errors([{"ts": time(), "id": "late", "summary": "late"}])
    assert history._pending_points
    assert history._pending_errors
    client.fail = False
    await history.write_point({"t": time(), "req": 2})
    loaded = await history.load_points(instance=instance_id(), start_ts=0, end_ts=time() + 10)
    assert {int(point["req"]) for point in loaded} == {2}
    errors = await history.load_errors(instance=instance_id(), start_ts=0, end_ts=time() + 10)
    assert any(item.get("id") == "late" for item in errors)
    assert not history._pending_points
    assert not history._pending_errors


@pytest.mark.asyncio
async def test_flush_pending_is_a_no_op_while_redis_is_down(monkeypatch):
    patch_history_redis(monkeypatch, FakeRedisClient(fail=True))
    await history.write_point({"t": time(), "req": 1})
    pending = len(history._pending_points)
    await history._flush_pending()
    assert len(history._pending_points) == pending
    assert history.history_available is False


@pytest.mark.asyncio
async def test_flush_pending_stops_when_persist_fails_again(monkeypatch):
    client = patch_history_redis(monkeypatch, FakeRedisClient(fail=True))
    await history.write_point({"t": time(), "req": 1})
    await history.write_errors([{"ts": time(), "summary": "late"}])
    history.history_available = True
    await history._flush_pending()
    assert history._pending_points
    client.fail = False
    client.fail_keys = frozenset({f"panel_mon:{instance_id()}:err"})
    history.history_available = True
    await history.write_point({"t": time(), "req": 2})
    assert history._pending_errors


@pytest.mark.asyncio
async def test_write_snapshot_failure_keeps_the_in_memory_copy(monkeypatch):
    patch_history_redis(monkeypatch, FakeRedisClient(fail=True))
    await history.write_snapshot({"ok": True})
    assert history.cached_snapshot() == {"ok": True}
    assert history.history_available is False


@pytest.mark.asyncio
async def test_load_snapshot_returns_cached_current_instance(monkeypatch):
    patch_history_redis(monkeypatch, FakeRedisClient(fail=True))
    history.remember_snapshot({"cached": True})
    assert await history.load_snapshot(instance_id()) == {"cached": True}


@pytest.mark.asyncio
async def test_touch_instance_failure_does_not_drop_the_point(monkeypatch):
    patch_history_redis(monkeypatch, FakeRedisClient(fail_keys=frozenset({"panel_mon:instances"})))
    await history.write_point({"t": time(), "req": 9})
    assert history.history_available is False
    loaded = await history.load_points(instance=instance_id(), start_ts=0, end_ts=time() + 10)
    assert any(int(point["req"]) == 9 for point in loaded)


@pytest.mark.asyncio
async def test_load_zset_skips_non_dict_members(monkeypatch):
    client = patch_history_redis(monkeypatch, FakeRedisClient())
    key = f"panel_mon:{instance_id()}:err"
    client.zsets[key] = [
        (1, 1.0),
        ("{", 2.0),
        ("[]", 3.0),
        (b'{"ts": 4, "summary": "ok"}', 4.0),
    ]
    loaded = await history.load_errors(instance=instance_id(), start_ts=0, end_ts=time() + 10)
    assert loaded == [{"ts": 4, "summary": "ok"}]


@pytest.mark.asyncio
async def test_current_instance_errors_come_from_memory_when_redis_is_down(monkeypatch):
    patch_history_redis(monkeypatch, FakeRedisClient(fail=True))
    now = time()
    await history.write_errors([{"ts": now, "summary": "mem"}])
    loaded = await history.load_errors(instance=instance_id(), start_ts=0, end_ts=now + 10)
    assert loaded[-1]["summary"] == "mem"


@pytest.mark.asyncio
async def test_error_cap_increments_dropped_count(monkeypatch):
    import sys

    monkeypatch.setattr(sys.modules["services.panel_monitor.history"], "MAX_ERRORS", 2)
    patch_history_redis(monkeypatch, FakeRedisClient())
    now = time()
    await history.write_errors(
        [
            {"ts": now - 2, "id": "a", "summary": "a"},
            {"ts": now - 1, "id": "b", "summary": "b"},
            {"ts": now, "id": "c", "summary": "c"},
        ]
    )
    assert history.dropped_errors == 1


@pytest.mark.asyncio
async def test_memory_error_overflow_counts_when_redis_is_down(monkeypatch):
    history._memory_errors = deque(maxlen=2)
    patch_history_redis(monkeypatch, FakeRedisClient(fail=True))
    now = time()
    await history.write_errors(
        [
            {"ts": now - 2, "summary": "a"},
            {"ts": now - 1, "summary": "b"},
            {"ts": now, "summary": "c"},
        ]
    )
    assert history.dropped_errors == 1
    assert history.history_available is False


@pytest.mark.asyncio
async def test_collect_once_does_not_write_when_disabled():
    from services.panel_monitor.collector import collect_once

    set_enabled(False)
    result = await collect_once()
    assert result == {}
    assert history.cached_snapshot() is None
