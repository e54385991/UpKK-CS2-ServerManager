"""Panel monitor aggregation, alerts, and history fallback."""

from __future__ import annotations

from time import time
from types import SimpleNamespace

import pytest

from modules.observability import clear_all, instance_id, reset_for_tests, set_enabled
from services.panel_monitor.aggregation import display_series
from services.panel_monitor.alerts import evaluate_alerts
from services.panel_monitor.history import history
from services.panel_monitor.queries import get_monitor
from services.panel_monitor.types import MonitorQuery


@pytest.fixture(autouse=True)
def _reset():
    reset_for_tests()
    clear_all()
    history.clear_buffers()
    history.history_available = True
    history.history_error = None
    history.last_sample_at = None
    history._snapshot = None
    yield
    reset_for_tests()
    clear_all()
    history.clear_buffers()
    history._snapshot = None


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
    assert (
        evaluate_alerts(
            quiet * 3,
            enabled=True,
            stale=False,
            redis_connected=True,
            unhandled=0,
            failed_tasks=0,
            log_errors=0,
        )
        == []
    )
    slow = [{"req": 20, "p95": 600, "s5": 0, "lag": 10, "dbx": 1, "dbc": 10}]
    watch = evaluate_alerts(
        slow * 3,
        enabled=True,
        stale=False,
        redis_connected=True,
        unhandled=0,
        failed_tasks=0,
        log_errors=0,
    )
    assert any(alert.id == "request_p95" and alert.severity == "watch" for alert in watch)
    errors = evaluate_alerts(
        [{"req": 4, "p95": 10, "s5": 1, "lag": 1, "dbx": 1, "dbc": 10}],
        enabled=True,
        stale=False,
        redis_connected=True,
        unhandled=0,
        failed_tasks=0,
        log_errors=0,
    )
    assert any(alert.id == "http_5xx" and alert.severity == "watch" for alert in errors)
    disabled = evaluate_alerts(
        slow * 3,
        enabled=False,
        stale=False,
        redis_connected=False,
        unhandled=3,
        failed_tasks=1,
        log_errors=4,
    )
    assert disabled == []


@pytest.mark.asyncio
async def test_history_falls_back_to_memory_when_redis_fails(monkeypatch):
    class Broken:
        async def zadd(self, *args, **kwargs):
            raise ConnectionError("down")

        async def zremrangebyscore(self, *args, **kwargs):
            raise ConnectionError("down")

        async def expire(self, *args, **kwargs):
            raise ConnectionError("down")

        async def zrangebyscore(self, *args, **kwargs):
            raise ConnectionError("down")

        async def get(self, *args, **kwargs):
            raise ConnectionError("down")

    fake = SimpleNamespace(client=Broken(), prefixed_key=lambda key: key)
    import sys

    monkeypatch.setattr(sys.modules["services.panel_monitor.history"], "redis_manager", fake)
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
    class Store:
        def __init__(self) -> None:
            self.fail = True
            self.zsets: dict[str, list[tuple[str, float]]] = {}
            self.kv: dict[str, str] = {}

        async def zadd(self, key, mapping):
            if self.fail:
                raise ConnectionError("down")
            items = self.zsets.setdefault(key, [])
            for member, score in mapping.items():
                items[:] = [(item, value) for item, value in items if item != member]
                items.append((str(member), float(score)))

        async def zremrangebyscore(self, key, _lo, _hi):
            if self.fail:
                raise ConnectionError("down")

        async def zremrangebyrank(self, key, _start, _stop):
            if self.fail:
                raise ConnectionError("down")

        async def expire(self, key, _ttl):
            if self.fail:
                raise ConnectionError("down")

        async def zrangebyscore(self, key, lo, hi, withscores=False):
            if self.fail:
                raise ConnectionError("down")
            items = [
                (member, score) for member, score in self.zsets.get(key, []) if lo <= score <= hi
            ]
            if withscores:
                return items
            return [member for member, _score in items]

        async def get(self, key):
            if self.fail:
                raise ConnectionError("down")
            return self.kv.get(key)

        async def set(self, key, value, ex=None):
            if self.fail:
                raise ConnectionError("down")
            self.kv[key] = value

    import sys

    store = Store()
    fake = SimpleNamespace(client=store, prefixed_key=lambda key: key)
    monkeypatch.setattr(sys.modules["services.panel_monitor.history"], "redis_manager", fake)
    await history.write_point({"t": time(), "req": 1})
    assert history.history_available is False
    store.fail = False
    await history.write_point({"t": time(), "req": 2})
    assert history.history_available is True
    loaded = await history.load_points(instance=instance_id(), start_ts=0, end_ts=time() + 10)
    assert {int(point["req"]) for point in loaded} >= {1, 2}


@pytest.mark.asyncio
async def test_collect_once_does_not_write_when_disabled():
    from services.panel_monitor.collector import collect_once

    set_enabled(False)
    result = await collect_once()
    assert result == {}
    assert history.cached_snapshot() is None
