"""Ten-second collection of compact history points."""

from __future__ import annotations

import logging
import os
import resource
from time import perf_counter, time
from typing import Any

from modules.observability import (
    drain_counters,
    drain_errors,
    drain_log_queue,
    drain_request_buckets,
    increment,
    is_enabled,
    snapshot_current_bucket,
)
from services.panel_metrics import capture_panel_performance
from services.panel_monitor.history import history

logger = logging.getLogger(__name__)
BUCKET_SECONDS = 10


async def collect_once() -> dict[str, Any]:
    """Capture one sample. Must not be called when monitoring is disabled."""
    if not is_enabled():
        return {}
    started = perf_counter()
    drain_log_queue()
    buckets = drain_request_buckets()
    counters = drain_counters()
    errors = drain_errors()
    snapshot = await capture_panel_performance()
    if not is_enabled():
        return snapshot
    _enrich_snapshot(snapshot, snapshot_current_bucket(), counters)
    now = time()
    if not buckets:
        point = compact_point(now, {}, counters, snapshot)
        await history.write_point(point)
    else:
        empty_counters = {"counts": {}, "percentiles": {}, "loop_lag_ms": {}}
        last_index = len(buckets) - 1
        for index, bucket in enumerate(buckets):
            point = compact_point(
                float(bucket["start_ts"]),
                bucket,
                counters if index == last_index else empty_counters,
                snapshot,
            )
            await history.write_point(point)
    await history.write_snapshot(snapshot)
    await history.write_errors(errors)
    elapsed_ms = (perf_counter() - started) * 1000
    history.last_collection_ms = elapsed_ms
    snapshot.setdefault("monitor", {})
    if isinstance(snapshot["monitor"], dict):
        snapshot["monitor"]["collection_ms"] = elapsed_ms
    return snapshot


def compact_point(
    ts: float,
    bucket: dict[str, Any],
    counters: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    process = snapshot.get("process") or {}
    database = snapshot.get("database") or {}
    redis_view = snapshot.get("redis") or {}
    operations = snapshot.get("operations") or {}
    latency = bucket.get("latency_ms") or {}
    counts = counters.get("counts") or {}
    loop = counters.get("loop_lag_ms") or {}
    checked_out = database.get("checked_out")
    capacity = database.get("capacity") or (
        int(database.get("pool_size") or 0) + int(database.get("max_overflow") or 0)
    )
    return {
        "t": ts,
        "req": int(bucket.get("requests") or 0),
        "s5": int(bucket.get("status_5xx") or 0),
        "s4": int(bucket.get("status_4xx") or 0),
        "unh": int(bucket.get("unhandled") or 0),
        "p50": latency.get("p50"),
        "p95": latency.get("p95"),
        "p99": latency.get("p99"),
        "max": latency.get("max"),
        "part": bool(bucket.get("latency_partial")),
        "cpu": process.get("cpu_percent"),
        "rss": process.get("rss_bytes"),
        "lag": loop.get("p95") if loop.get("p95") else process.get("event_loop_lag_ms"),
        "dbx": checked_out,
        "dbc": capacity,
        "rdok": redis_view.get("connected"),
        "rdms": redis_view.get("ping_ms"),
        "qrun": int(operations.get("running") or 0),
        "qwait": int(operations.get("queued") or 0),
        "qage": operations.get("oldest_queue_ms"),
        "tfail": int(counts.get("task.failed") or 0),
        "fd": process.get("fd_open"),
        "fdmax": process.get("fd_limit"),
    }


def _enrich_snapshot(
    snapshot: dict[str, Any],
    current_bucket: dict[str, Any] | None,
    counters: dict[str, Any],
) -> None:
    process = snapshot.setdefault("process", {})
    fd_open, fd_limit = fd_usage()
    process["fd_open"] = fd_open
    process["fd_limit"] = fd_limit
    loop = counters.get("loop_lag_ms") or {}
    if loop.get("p95"):
        process["event_loop_lag_p95_ms"] = loop.get("p95")
        process["event_loop_lag_max_ms"] = loop.get("max")
    database = snapshot.setdefault("database", {})
    capacity = int(database.get("pool_size") or 0) + int(database.get("max_overflow") or 0)
    database["capacity"] = capacity
    counts = counters.get("counts") or {}
    percentiles = counters.get("percentiles") or {}
    database["invalidations"] = int(counts.get("db.invalidations") or 0)
    database["slow_executions"] = int(counts.get("db.slow") or 0)
    database["errors"] = int(counts.get("db.errors") or 0)
    database["execute_p95_ms"] = (percentiles.get("db.execute_ms") or {}).get("p95")
    redis_view = snapshot.setdefault("redis", {})
    redis_view["failures"] = int(counts.get("redis.failures") or 0)
    redis_view["timeouts"] = int(counts.get("redis.timeouts") or 0)
    redis_view["monitor_ops"] = int(counts.get("redis.monitor_ops") or 0)
    redis_view["op_p95_ms"] = (percentiles.get("redis.op_ms") or {}).get("p95")
    requests = snapshot.setdefault("requests", {})
    if current_bucket:
        requests["in_flight"] = int(current_bucket.get("in_flight") or 0)
        requests["status_401"] = int(current_bucket.get("status_401") or 0)
        requests["status_403"] = int(current_bucket.get("status_403") or 0)
        requests["status_404"] = int(current_bucket.get("status_404") or 0)
        requests["status_409"] = int(current_bucket.get("status_409") or 0)
        requests["status_422"] = int(current_bucket.get("status_422") or 0)
        requests["status_429"] = int(current_bucket.get("status_429") or 0)
        requests["unhandled_exceptions"] = int(current_bucket.get("unhandled") or 0)
        requests["stream_errors"] = int(current_bucket.get("stream_errors") or 0)
        requests["cancellations"] = int(current_bucket.get("cancellations") or 0)
        requests["latency_partial"] = bool(current_bucket.get("latency_partial"))
    snapshot["outbound_http"] = _outbound_groups(counts, percentiles)
    operations = snapshot.setdefault("operations", {})
    operations["submitted"] = int(counts.get("task.submitted") or 0)
    operations["succeeded"] = int(counts.get("task.succeeded") or 0)
    operations["failed"] = int(counts.get("task.failed") or 0)
    operations["cancelled"] = int(counts.get("task.cancelled") or 0)
    ssh = snapshot.setdefault("ssh_pool", {})
    ssh["reuses"] = int(counts.get("ssh.reuses") or 0)
    ssh["auth_failures"] = int(counts.get("ssh.auth_failures") or 0)
    ssh["timeouts"] = int(counts.get("ssh.timeouts") or 0)
    ssh["reconnect_attempts"] = int(counts.get("ssh.reconnect_attempts") or 0)
    ssh["reconnect_failures"] = int(counts.get("ssh.reconnect_failures") or 0)
    ssh["connect_p95_ms"] = (percentiles.get("ssh.connect_ms") or {}).get("p95")
    for limiter in snapshot.get("limiters") or []:
        if not isinstance(limiter, dict):
            continue
        wait_key = (
            "limiter.ssh.wait_ms"
            if limiter.get("name") == "ssh_probes"
            else f"limiter.{limiter.get('name')}.wait_ms"
        )
        limiter["wait_p95_ms"] = (percentiles.get(wait_key) or {}).get("p95")
    snapshot["monitor"] = {
        "collection_ms": history.last_collection_ms,
        "history_available": history.history_available,
        "history_error": history.history_error,
        "dropped_error_details": history.dropped_errors,
        "truncated_summaries": history.truncated_summaries,
    }


def _outbound_groups(counts: dict[str, int], percentiles: dict[str, Any]) -> list[dict[str, Any]]:
    groups = []
    for group in ("github", "download", "ai", "other"):
        groups.append(
            {
                "group": group,
                "calls": int(counts.get(f"http.{group}.calls") or 0),
                "timeouts": int(counts.get(f"http.{group}.timeouts") or 0),
                "network_errors": int(counts.get(f"http.{group}.network") or 0),
                "status_429": int(counts.get(f"http.{group}.status_429") or 0),
                "status_5xx": int(counts.get(f"http.{group}.status_5xx") or 0),
                "retries": int(counts.get(f"http.{group}.retries") or 0),
                "p95_ms": (percentiles.get(f"http.{group}.ms") or {}).get("p95"),
            }
        )
    return groups


def fd_usage() -> tuple[int | None, int | None]:
    used: int | None
    try:
        used = len(os.listdir("/proc/self/fd"))
    except OSError:
        used = None
    try:
        soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        limit = None if soft == resource.RLIM_INFINITY else int(soft)
    except OSError, ValueError:
        limit = None
    return used, limit


def record_collect_failure() -> None:
    increment("monitor.collect_fail")
    logger.exception("Panel monitor collection failed")
