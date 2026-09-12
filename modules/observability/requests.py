"""Fixed 10-second HTTP buckets. No I/O."""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from time import time
from typing import Any

from .control import generation as current_switch_generation
from .control import is_enabled
from .stats import percentile_block

BUCKET_SECONDS = 10
SAMPLE_LIMIT = 4096
SLOW_REQUEST_MS = 500
MAX_ROUTE_ROWS = 20
MAX_OPEN_BUCKETS = 8


@dataclass
class _RouteAgg:
    count: int = 0
    error_count: int = 0
    durations: list[float] = field(default_factory=list)


@dataclass
class RequestBucket:
    start_ts: float
    generation: int
    requests: int = 0
    http_count: int = 0
    sse_count: int = 0
    status_2xx: int = 0
    status_4xx: int = 0
    status_5xx: int = 0
    status_401: int = 0
    status_403: int = 0
    status_404: int = 0
    status_409: int = 0
    status_422: int = 0
    status_429: int = 0
    unhandled: int = 0
    stream_errors: int = 0
    cancellations: int = 0
    slow_count: int = 0
    samples_dropped: int = 0
    durations: list[float] = field(default_factory=list)
    sse_first_byte_ms: list[float] = field(default_factory=list)
    sse_duration_ms: list[float] = field(default_factory=list)
    routes: dict[tuple[str, str], _RouteAgg] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        duration_block = percentile_block(self.durations)
        span = BUCKET_SECONDS
        rpm = (self.requests / span) * 60.0 if self.requests else 0.0
        return {
            "start_ts": self.start_ts,
            "generation": self.generation,
            "requests": self.requests,
            "http_count": self.http_count,
            "sse_count": self.sse_count,
            "status_2xx": self.status_2xx,
            "status_4xx": self.status_4xx,
            "status_5xx": self.status_5xx,
            "status_401": self.status_401,
            "status_403": self.status_403,
            "status_404": self.status_404,
            "status_409": self.status_409,
            "status_422": self.status_422,
            "status_429": self.status_429,
            "unhandled": self.unhandled,
            "stream_errors": self.stream_errors,
            "cancellations": self.cancellations,
            "slow_count": self.slow_count,
            "samples_dropped": self.samples_dropped,
            "latency_partial": self.samples_dropped > 0,
            "latency_ms": duration_block,
            "sse_first_byte_ms": percentile_block(self.sse_first_byte_ms),
            "sse_duration_ms": percentile_block(self.sse_duration_ms),
            "requests_per_minute": rpm,
            "error_rate": (self.status_5xx / self.requests) if self.requests else 0.0,
            "by_route": _route_rows(self.routes),
        }


_lock = threading.Lock()
_buckets: dict[float, RequestBucket] = {}
_in_flight = 0
_in_flight_max = 0


def _bucket_start(ts: float) -> float:
    return float(int(ts // BUCKET_SECONDS) * BUCKET_SECONDS)


def _bucket(now: float | None = None) -> RequestBucket | None:
    if not is_enabled():
        return None
    ts = time() if now is None else now
    start = _bucket_start(ts)
    bucket = _buckets.get(start)
    if bucket is None or bucket.generation != current_switch_generation():
        bucket = RequestBucket(start_ts=start, generation=current_switch_generation())
        _buckets[start] = bucket
        _prune_open_buckets()
    return bucket


def _prune_open_buckets() -> None:
    if len(_buckets) <= MAX_OPEN_BUCKETS:
        return
    for key in sorted(_buckets)[:-MAX_OPEN_BUCKETS]:
        _buckets.pop(key, None)


def begin_request() -> int:
    global _in_flight, _in_flight_max
    if not is_enabled():
        return 0
    with _lock:
        _in_flight += 1
        _in_flight_max = max(_in_flight_max, _in_flight)
        return _in_flight


def end_request() -> None:
    global _in_flight
    if not is_enabled() and _in_flight <= 0:
        return
    with _lock:
        _in_flight = max(0, _in_flight - 1)


def in_flight() -> int:
    return _in_flight


def record_http(
    *,
    method: str,
    route: str,
    status: int,
    duration_ms: float,
    generation: int,
    sse: bool = False,
    first_byte_ms: float | None = None,
    ts: float | None = None,
) -> None:
    if not is_enabled() or generation != current_switch_generation():
        return
    with _lock:
        bucket = _bucket(now=ts)
        if bucket is None:
            return
        bucket.requests += 1
        if sse:
            bucket.sse_count += 1
        else:
            bucket.http_count += 1
        _count_status(bucket, status)
        if duration_ms >= SLOW_REQUEST_MS:
            bucket.slow_count += 1
        if len(bucket.durations) < SAMPLE_LIMIT:
            bucket.durations.append(duration_ms)
        else:
            bucket.samples_dropped += 1
        if sse and first_byte_ms is not None and len(bucket.sse_first_byte_ms) < SAMPLE_LIMIT:
            bucket.sse_first_byte_ms.append(first_byte_ms)
        if sse:
            bucket.sse_duration_ms.append(duration_ms)
        key = (method.upper(), route)
        agg = bucket.routes.get(key)
        if agg is None:
            agg = _RouteAgg()
            bucket.routes[key] = agg
        agg.count += 1
        if status >= 500:
            agg.error_count += 1
        if len(agg.durations) < SAMPLE_LIMIT:
            agg.durations.append(duration_ms)


def record_unhandled(*, generation: int, ts: float | None = None) -> None:
    if not is_enabled() or generation != current_switch_generation():
        return
    with _lock:
        bucket = _bucket(now=ts)
        if bucket is None:
            return
        bucket.unhandled += 1
        bucket.requests += 1
        bucket.status_5xx += 1


def record_stream_error(*, generation: int, ts: float | None = None) -> None:
    if not is_enabled() or generation != current_switch_generation():
        return
    with _lock:
        bucket = _bucket(now=ts)
        if bucket is None:
            return
        bucket.stream_errors += 1


def record_cancellation(*, generation: int, ts: float | None = None) -> None:
    if not is_enabled() or generation != current_switch_generation():
        return
    with _lock:
        bucket = _bucket(now=ts)
        if bucket is None:
            return
        bucket.cancellations += 1


def drain_request_buckets() -> list[dict[str, Any]]:
    """Pop closed buckets and return compact dicts. The current bucket stays."""
    global _in_flight_max
    now = time()
    current = _bucket_start(now)
    drained: list[RequestBucket] = []
    with _lock:
        for start in list(_buckets):
            if start < current:
                drained.append(_buckets.pop(start))
        in_flight_max = _in_flight_max
        _in_flight_max = _in_flight
    payload = [item.as_dict() for item in drained]
    for item in payload:
        item["in_flight_max"] = in_flight_max
    return payload


def snapshot_current_bucket() -> dict[str, Any] | None:
    with _lock:
        bucket = _bucket()
        if bucket is None:
            return None
        payload = bucket.as_dict()
        payload["in_flight"] = _in_flight
        payload["in_flight_max"] = _in_flight_max
        return payload


def clear_request_state() -> None:
    global _in_flight, _in_flight_max
    with _lock:
        _buckets.clear()
        _in_flight = 0
        _in_flight_max = 0


def _count_status(bucket: RequestBucket, status: int) -> None:
    if 200 <= status < 300:
        bucket.status_2xx += 1
    elif 400 <= status < 500:
        bucket.status_4xx += 1
    elif status >= 500:
        bucket.status_5xx += 1
    if status == 401:
        bucket.status_401 += 1
    elif status == 403:
        bucket.status_403 += 1
    elif status == 404:
        bucket.status_404 += 1
    elif status == 409:
        bucket.status_409 += 1
    elif status == 422:
        bucket.status_422 += 1
    elif status == 429:
        bucket.status_429 += 1


def _route_rows(routes: dict[tuple[str, str], _RouteAgg]) -> list[dict[str, Any]]:
    ranked = sorted(
        routes.items(),
        key=lambda item: (
            -percentile_block(item[1].durations)["p95"],
            -item[1].count,
        ),
    )
    rows: list[dict[str, Any]] = []
    for (method, route), agg in ranked[:MAX_ROUTE_ROWS]:
        rows.append(
            {
                "method": method,
                "route": route,
                "count": agg.count,
                "error_count": agg.error_count,
                "latency_ms": percentile_block(agg.durations),
            }
        )
    return rows


def window_samples_for_legacy() -> tuple[int, list[float], dict[str, int], list[dict[str, Any]]]:
    """Aggregate open buckets for the legacy snapshot window."""
    with _lock:
        buckets = list(_buckets.values())
    if not buckets:
        return 0, [], {}, []
    merged_routes: dict[tuple[str, str], _RouteAgg] = defaultdict(_RouteAgg)
    durations: list[float] = []
    counts = {
        "requests": 0,
        "status_2xx": 0,
        "status_4xx": 0,
        "status_5xx": 0,
        "slow_count": 0,
        "dropped": 0,
    }
    for bucket in buckets:
        counts["requests"] += bucket.requests
        counts["status_2xx"] += bucket.status_2xx
        counts["status_4xx"] += bucket.status_4xx
        counts["status_5xx"] += bucket.status_5xx
        counts["slow_count"] += bucket.slow_count
        counts["dropped"] += bucket.samples_dropped
        durations.extend(bucket.durations)
        for key, agg in bucket.routes.items():
            target = merged_routes[key]
            target.count += agg.count
            target.error_count += agg.error_count
            target.durations.extend(agg.durations)
    return counts["requests"], durations, counts, _route_rows(merged_routes)
