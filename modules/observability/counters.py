"""Named counters and bounded duration reservoirs. No I/O."""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from time import time
from typing import Any

from .control import generation as current_switch_generation
from .control import is_enabled
from .stats import percentile_block

SAMPLE_LIMIT = 4096
LOOP_LAG_LIMIT = 120
SLOW_DB_MS = 200

_lock = threading.Lock()
_counts: dict[str, int] = defaultdict(int)
_samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=SAMPLE_LIMIT))
_loop_lag: deque[float] = deque(maxlen=LOOP_LAG_LIMIT)


def _accept(generation: int | None = None) -> bool:
    if not is_enabled():
        return False
    if generation is None:
        return True
    return generation == current_switch_generation()


def increment(name: str, amount: int = 1, *, generation: int | None = None) -> None:
    if amount == 0 or not _accept(generation):
        return
    with _lock:
        _counts[name] += amount


def observe(name: str, value_ms: float, *, generation: int | None = None) -> None:
    if not _accept(generation):
        return
    with _lock:
        _samples[name].append(max(0.0, value_ms))


def record_loop_lag(lag_ms: float) -> None:
    if not is_enabled():
        return
    with _lock:
        _loop_lag.append(max(0.0, lag_ms))


def record_db_execute(duration_ms: float, *, error: bool = False) -> None:
    if not is_enabled():
        return
    with _lock:
        _counts["db.executions"] += 1
        _samples["db.execute_ms"].append(max(0.0, duration_ms))
        if duration_ms >= SLOW_DB_MS:
            _counts["db.slow"] += 1
        if error:
            _counts["db.errors"] += 1


def record_db_invalidate() -> None:
    increment("db.invalidations")


def record_redis(
    *,
    duration_ms: float | None = None,
    failed: bool = False,
    timeout: bool = False,
    monitor: bool = False,
) -> None:
    if not is_enabled():
        return
    with _lock:
        if monitor:
            _counts["redis.monitor_ops"] += 1
            return
        _counts["redis.ops"] += 1
        if duration_ms is not None:
            _samples["redis.op_ms"].append(max(0.0, duration_ms))
        if failed:
            _counts["redis.failures"] += 1
        if timeout:
            _counts["redis.timeouts"] += 1


def record_ssh(
    *,
    reused: bool = False,
    connect_ms: float | None = None,
    auth_failed: bool = False,
    timeout: bool = False,
    reconnect_attempt: bool = False,
    reconnect_failed: bool = False,
) -> None:
    if not is_enabled():
        return
    with _lock:
        if reused:
            _counts["ssh.reuses"] += 1
        if connect_ms is not None:
            _counts["ssh.connects"] += 1
            _samples["ssh.connect_ms"].append(max(0.0, connect_ms))
        if auth_failed:
            _counts["ssh.auth_failures"] += 1
        if timeout:
            _counts["ssh.timeouts"] += 1
        if reconnect_attempt:
            _counts["ssh.reconnect_attempts"] += 1
        if reconnect_failed:
            _counts["ssh.reconnect_failures"] += 1


def record_outbound(
    group: str,
    duration_ms: float,
    *,
    status: int | None = None,
    timeout: bool = False,
    network: bool = False,
    retry: bool = False,
) -> None:
    if not is_enabled():
        return
    key = group if group in {"github", "download", "ai", "other"} else "other"
    with _lock:
        _counts[f"http.{key}.calls"] += 1
        _samples[f"http.{key}.ms"].append(max(0.0, duration_ms))
        if retry:
            _counts[f"http.{key}.retries"] += 1
        if timeout:
            _counts[f"http.{key}.timeouts"] += 1
        if network:
            _counts[f"http.{key}.network"] += 1
        if status == 429:
            _counts[f"http.{key}.status_429"] += 1
        if status is not None and status >= 500:
            _counts[f"http.{key}.status_5xx"] += 1


def record_task(
    outcome: str, *, queue_ms: float | None = None, execute_ms: float | None = None
) -> None:
    if not is_enabled():
        return
    with _lock:
        _counts[f"task.{outcome}"] += 1
        if queue_ms is not None:
            _samples["task.queue_ms"].append(max(0.0, queue_ms))
        if execute_ms is not None:
            _samples["task.execute_ms"].append(max(0.0, execute_ms))


def record_limiter_wait(name: str, wait_ms: float) -> None:
    if not is_enabled():
        return
    with _lock:
        _samples[f"limiter.{name}.wait_ms"].append(max(0.0, wait_ms))


def drain_counters() -> dict[str, Any]:
    """Copy and reset counters/samples except the live loop-lag window."""
    with _lock:
        counts = dict(_counts)
        _counts.clear()
        samples = {name: list(values) for name, values in _samples.items()}
        for values in _samples.values():
            values.clear()
        loop = list(_loop_lag)
        _loop_lag.clear()
    percentiles = {name: percentile_block(values) for name, values in samples.items()}
    return {
        "ts": time(),
        "counts": counts,
        "percentiles": percentiles,
        "loop_lag_ms": percentile_block(loop),
        "loop_lag_latest": loop[-1] if loop else None,
    }


def samples_items() -> list[tuple[str, list[float]]]:
    return [(name, list(values)) for name, values in _samples.items()]


def loop_lag_latest() -> float | None:
    with _lock:
        if not _loop_lag:
            return None
        return _loop_lag[-1]


def snapshot_counts() -> dict[str, int]:
    with _lock:
        return dict(_counts)


def clear_counters() -> None:
    with _lock:
        _counts.clear()
        _samples.clear()
        _loop_lag.clear()
