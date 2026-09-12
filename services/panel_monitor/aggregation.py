"""Downsample 10-second points into display buckets without averaging percentiles."""

from __future__ import annotations

from typing import Any

from .types import DISPLAY_BUCKET_SECONDS, RANGE_SECONDS


def display_series(points: list[dict[str, Any]], range_key: str) -> list[dict[str, Any]]:
    width = DISPLAY_BUCKET_SECONDS.get(range_key, 30)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for point in points:
        ts = float(point.get("t") or 0)
        bucket = int(ts // width) * width
        grouped.setdefault(bucket, []).append(point)
    series: list[dict[str, Any]] = []
    for start in sorted(grouped):
        series.append(_merge_bucket(start, grouped[start], width))
    return series


def range_start(now: float, range_key: str) -> float:
    return now - RANGE_SECONDS.get(range_key, 3600)


def _merge_bucket(start: float, points: list[dict[str, Any]], width: int) -> dict[str, Any]:
    last = points[-1]
    requests = sum(int(point.get("req") or 0) for point in points)
    status_5xx = sum(int(point.get("s5") or 0) for point in points)
    unhandled = sum(int(point.get("unh") or 0) for point in points)
    failed_tasks = sum(int(point.get("tfail") or 0) for point in points)
    duration = max(width, 1)
    p95_values = [float(point["p95"]) for point in points if point.get("p95") is not None]
    lag_values = [float(point["lag"]) for point in points if point.get("lag") is not None]
    rss_values = [int(point["rss"]) for point in points if point.get("rss") is not None]
    return {
        "ts": start,
        "request_count": requests,
        "requests_per_minute": (requests / duration) * 60.0 if requests else 0.0,
        "status_5xx": status_5xx,
        "error_rate": (status_5xx / requests) if requests else 0.0,
        "latency_p50_ms": _last_present(points, "p50"),
        "latency_p95_ms": max(p95_values) if p95_values else None,
        "latency_p99_ms": _peak(points, "p99"),
        "latency_max_ms": _peak(points, "max"),
        "latency_partial": any(bool(point.get("part")) for point in points),
        "cpu_percent": last.get("cpu"),
        "rss_bytes": last.get("rss"),
        "rss_peak_bytes": max(rss_values) if rss_values else None,
        "loop_lag_p95_ms": max(lag_values) if lag_values else None,
        "db_checked_out": last.get("dbx"),
        "db_capacity": last.get("dbc"),
        "redis_ping_ms": last.get("rdms"),
        "redis_connected": last.get("rdok"),
        "queue_running": int(last.get("qrun") or 0),
        "queue_queued": int(last.get("qwait") or 0),
        "oldest_queue_ms": last.get("qage"),
        "failed_tasks": failed_tasks,
        "unhandled_exceptions": unhandled,
        "fd_open": last.get("fd"),
        "fd_limit": last.get("fdmax"),
    }


def _last_present(points: list[dict[str, Any]], key: str) -> float | None:
    for point in reversed(points):
        value = point.get(key)
        if value is not None:
            return float(value)
    return None


def _peak(points: list[dict[str, Any]], key: str) -> float | None:
    values = [float(point[key]) for point in points if point.get(key) is not None]
    return max(values) if values else None
