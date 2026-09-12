"""In-process panel performance samples for administrator diagnostics.

The collector is process-local: multi-worker deployments each report their own
window. Snapshots never include host addresses, credentials, or request bodies.
"""

from __future__ import annotations

import asyncio
import os
import resource
import sys
import threading
from collections import defaultdict, deque
from datetime import UTC, datetime
from time import monotonic, time
from typing import Final, NamedTuple

from modules.config import get_settings
from modules.database import engine
from services.a2s_cache_service import a2s_cache_service
from services.redis_manager import redis_manager
from services.server_operation_hub import server_operation_hub
from services.ssh_connection_pool import ssh_connection_pool
from services.telemetry_runtime import ssh_probe_limiter

FORMAT: Final = "upkk-panel-performance"
SCHEMA_VERSION: Final = 1
PRIORITY_SECTIONS: Final[tuple[str, ...]] = (
    "requests",
    "process",
    "database",
    "redis",
    "ssh_pool",
    "limiters",
    "operations",
    "runtime",
)
SAMPLE_LIMIT: Final = 4096
WINDOW_SECONDS: Final = 900
SLOW_REQUEST_MS: Final = 500
MAX_ROUTE_ROWS: Final = 20
LOOP_LAG_LIMIT: Final = 120
LOOP_LAG_INTERVAL_SECONDS: Final = 1.0

_SKIP_EXACT = frozenset({"/health", "/openapi.json", "/docs", "/redoc"})
_SKIP_PREFIXES = ("/static", "/api/v1/diagnostics")


class RequestSample(NamedTuple):
    ts: float
    method: str
    route: str
    status: int
    duration_ms: float


class PanelMetricsStore:
    """Bounded request and event-loop samples for one process."""

    def __init__(self) -> None:
        self.started_at = time()
        self.started_monotonic = monotonic()
        self._requests: deque[RequestSample] = deque(maxlen=SAMPLE_LIMIT)
        self._loop_lag_ms: deque[float] = deque(maxlen=LOOP_LAG_LIMIT)
        self._cpu_sample: tuple[float, float] | None = None

    def clear(self) -> None:
        """Drop recorded samples. Used by unit tests."""
        self._requests.clear()
        self._loop_lag_ms.clear()
        self._cpu_sample = None

    def record_request(
        self,
        *,
        method: str,
        route: str,
        status: int,
        duration_ms: float,
        ts: float | None = None,
    ) -> None:
        self._requests.append(
            RequestSample(
                ts=monotonic() if ts is None else ts,
                method=method,
                route=route,
                status=status,
                duration_ms=duration_ms,
            )
        )

    def record_loop_lag(self, lag_ms: float) -> None:
        self._loop_lag_ms.append(max(0.0, lag_ms))

    def request_window(self, now: float | None = None) -> list[RequestSample]:
        cutoff = (monotonic() if now is None else now) - WINDOW_SECONDS
        return [sample for sample in self._requests if sample.ts >= cutoff]

    def loop_lag_latest(self) -> float | None:
        if not self._loop_lag_ms:
            return None
        return self._loop_lag_ms[-1]

    def take_cpu_percent(self) -> float | None:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        cpu_seconds = usage.ru_utime + usage.ru_stime
        now = monotonic()
        previous = self._cpu_sample
        self._cpu_sample = (now, cpu_seconds)
        if previous is None:
            return None
        elapsed = now - previous[0]
        if elapsed <= 0:
            return None
        return max(0.0, (cpu_seconds - previous[1]) / elapsed * 100.0)


metrics_store = PanelMetricsStore()

_sampler_stop: asyncio.Event | None = None
_sampler_task: asyncio.Task[None] | None = None


def should_record_path(path: str) -> bool:
    """Skip liveness and self-observation so probes do not pollute percentiles."""
    if path in _SKIP_EXACT:
        return False
    return not path.startswith(_SKIP_PREFIXES)


def route_label(path: str, template: str | None = None) -> str:
    """Prefer the matched route template; fall back to id-normalized paths."""
    if template:
        return template
    parts: list[str] = []
    for part in path.split("/"):
        if part.isdigit() or _looks_like_uuid(part):
            parts.append("{id}")
        else:
            parts.append(part)
    return "/".join(parts) or "/"


def _looks_like_uuid(value: str) -> bool:
    if len(value) != 36:
        return False
    hex_parts = value.split("-")
    if [len(part) for part in hex_parts] != [8, 4, 4, 4, 12]:
        return False
    return all(
        part and all(char in "0123456789abcdefABCDEF" for char in part) for part in hex_parts
    )


def percentiles(values: list[float]) -> dict[str, float]:
    """Nearest-rank p50/p95/p99 plus the observed max."""
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    ordered = sorted(values)
    last = len(ordered) - 1
    return {
        "p50": ordered[_rank(last, 50)],
        "p95": ordered[_rank(last, 95)],
        "p99": ordered[_rank(last, 99)],
        "max": ordered[last],
    }


def _rank(last_index: int, percent: int) -> int:
    if last_index <= 0:
        return 0
    return min(last_index, max(0, int((percent / 100) * last_index)))


def _latency_block(values: list[float]) -> dict[str, float]:
    return percentiles(values)


def _route_rows(samples: list[RequestSample]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[RequestSample]] = defaultdict(list)
    for sample in samples:
        grouped[(sample.method, sample.route)].append(sample)
    ranked = sorted(
        grouped.items(),
        key=lambda item: (
            -percentiles([row.duration_ms for row in item[1]])["p95"],
            -len(item[1]),
        ),
    )
    rows: list[dict[str, object]] = []
    for (method, route), group in ranked[:MAX_ROUTE_ROWS]:
        rows.append(
            {
                "method": method,
                "route": route,
                "count": len(group),
                "error_count": sum(1 for row in group if row.status >= 500),
                "latency_ms": _latency_block([row.duration_ms for row in group]),
            }
        )
    return rows


def request_metrics_payload(samples: list[RequestSample]) -> dict[str, object]:
    count = len(samples)
    status_2xx = sum(1 for sample in samples if 200 <= sample.status < 300)
    status_4xx = sum(1 for sample in samples if 400 <= sample.status < 500)
    status_5xx = sum(1 for sample in samples if sample.status >= 500)
    durations = [sample.duration_ms for sample in samples]
    span_seconds = WINDOW_SECONDS
    if len(samples) >= 2:
        span_seconds = max(1.0, samples[-1].ts - samples[0].ts)
    return {
        "window_seconds": WINDOW_SECONDS,
        "sample_count": count,
        "requests_per_minute": (count / span_seconds) * 60.0 if count else 0.0,
        "status_2xx": status_2xx,
        "status_4xx": status_4xx,
        "status_5xx": status_5xx,
        "error_rate": (status_5xx / count) if count else 0.0,
        "latency_ms": _latency_block(durations),
        "slow_request_count": sum(1 for sample in samples if sample.duration_ms >= SLOW_REQUEST_MS),
        "slow_request_threshold_ms": SLOW_REQUEST_MS,
        "by_route": _route_rows(samples),
    }


def current_rss_bytes() -> int | None:
    """Current RSS when the host exposes it; otherwise peak RSS as a fallback."""
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        with open("/proc/self/statm", encoding="ascii") as handle:
            resident_pages = int(handle.read().split()[1])
        return resident_pages * page_size
    except OSError, ValueError, IndexError, AttributeError:
        pass
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss = int(usage.ru_maxrss)
    if sys.platform == "darwin":
        return rss
    return rss * 1024


def _asyncio_task_count() -> int | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return len(asyncio.all_tasks(loop))


def process_metrics_payload() -> dict[str, object]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "pid": os.getpid(),
        "uptime_seconds": max(0.0, monotonic() - metrics_store.started_monotonic),
        "rss_bytes": current_rss_bytes(),
        "cpu_user_seconds": usage.ru_utime,
        "cpu_system_seconds": usage.ru_stime,
        "cpu_percent": metrics_store.take_cpu_percent(),
        "threads": threading.active_count(),
        "asyncio_tasks": _asyncio_task_count(),
        "event_loop_lag_ms": metrics_store.loop_lag_latest(),
    }


def database_pool_payload() -> dict[str, object]:
    settings = get_settings()
    pool = engine.sync_engine.pool
    return {
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        "checked_out": _pool_metric(pool, "checkedout"),
        "checked_in": _pool_metric(pool, "checkedin"),
        "overflow": _pool_metric(pool, "overflow"),
    }


def _pool_metric(pool: object, name: str) -> int | None:
    reader = getattr(pool, name, None)
    if not callable(reader):
        return None
    try:
        value = reader()
    except Exception:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def limiter_payload(name: str, snapshot: dict[str, int]) -> dict[str, object]:
    return {
        "name": name,
        "global_limit": int(snapshot.get("global_limit") or 0),
        "per_key_limit": int(snapshot.get("per_key_limit") or 0),
        "active_keys": int(snapshot.get("active_keys") or 0),
        "borrowers": int(snapshot.get("borrowers") or 0),
    }


async def _redis_payload() -> dict[str, object]:
    settings = get_settings()
    started = monotonic()
    try:
        connected = await redis_manager.ping()
        ping_ms = (monotonic() - started) * 1000
    except Exception:
        connected = False
        ping_ms = None
    return {
        "connected": connected,
        "ping_ms": ping_ms,
        "pool_max_connections": settings.REDIS_POOL_SIZE,
    }


async def _ssh_pool_payload() -> dict[str, int]:
    stats = await ssh_connection_pool.get_pool_stats()
    return {
        "connections": int(stats.get("alive_connections") or 0),
        "in_use": int(stats.get("in_use_connections") or 0),
        "idle": int(stats.get("idle_connections") or 0),
        "leases": int(stats.get("active_leases") or 0),
        "draining": int(stats.get("draining_connections") or 0),
        "idle_timeout": int(stats.get("idle_timeout") or 900),
        "max_lifetime": int(stats.get("max_lifetime") or 3600),
        "keepalive_interval": int(stats.get("keepalive_interval") or 30),
        "keepalive_count_max": int(stats.get("keepalive_count_max") or 3),
    }


async def capture_panel_performance() -> dict[str, object]:
    """Assemble a JSON-ready snapshot. Redis ping and SSH stats run together."""
    redis_view, ssh_view = await asyncio.gather(_redis_payload(), _ssh_pool_payload())
    return {
        "format": FORMAT,
        "version": SCHEMA_VERSION,
        "captured_at": datetime.now(UTC),
        "priority": list(PRIORITY_SECTIONS),
        "requests": request_metrics_payload(metrics_store.request_window()),
        "process": process_metrics_payload(),
        "database": database_pool_payload(),
        "redis": redis_view,
        "ssh_pool": ssh_view,
        "limiters": [
            limiter_payload("ssh_probes", ssh_probe_limiter.snapshot()),
            limiter_payload("a2s", a2s_cache_service.limiter_snapshot()),
        ],
        "operations": server_operation_hub.occupancy_snapshot(),
    }


async def _loop_lag_loop(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        started = loop.time()
        try:
            await asyncio.wait_for(stop.wait(), timeout=LOOP_LAG_INTERVAL_SECONDS)
            return
        except TimeoutError:
            delay = loop.time() - started - LOOP_LAG_INTERVAL_SECONDS
            metrics_store.record_loop_lag(delay * 1000)


async def start_loop_sampler() -> None:
    global _sampler_stop, _sampler_task
    if _sampler_task is not None and not _sampler_task.done():
        return
    _sampler_stop = asyncio.Event()
    _sampler_task = asyncio.create_task(_loop_lag_loop(_sampler_stop))


async def stop_loop_sampler() -> None:
    global _sampler_stop, _sampler_task
    if _sampler_stop is not None:
        _sampler_stop.set()
    task = _sampler_task
    _sampler_task = None
    _sampler_stop = None
    if task is not None:
        await asyncio.gather(task, return_exceptions=True)
