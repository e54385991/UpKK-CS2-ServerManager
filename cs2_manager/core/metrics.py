"""Small, dependency-free Prometheus metrics primitives.

The registry is intentionally owned by an application instance.  This keeps
tests and multiple factory-created applications isolated while still exposing
the process-local metrics appropriate for the supported single-worker runtime.
"""

from __future__ import annotations

import asyncio
import math
import re
import threading
from dataclasses import dataclass

from .container import AppContainer

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
_DIGIT_RUN = re.compile(r"\d+")


@dataclass(slots=True)
class _Summary:
    count: int = 0
    total: float = 0.0


class MetricsRegistry:
    """Record bounded-cardinality HTTP and background-task measurements."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._http: dict[tuple[str, str, str, str], _Summary] = {}
        self._tasks: dict[tuple[str, str], _Summary] = {}

    def observe_http(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        outcome = "success" if status_code < 500 else "error"
        key = (method.upper(), route, str(status_code), outcome)
        self._observe(self._http, key, duration_seconds)

    def observe_background_task(
        self,
        *,
        name: str,
        outcome: str,
        duration_seconds: float,
    ) -> None:
        # Task names often contain a server ID or an asyncio-generated suffix.
        # Collapse digit runs to avoid an unbounded Prometheus label set.
        bounded_name = _DIGIT_RUN.sub("#", name)[:128] or "unnamed"
        self._observe(self._tasks, (bounded_name, outcome), duration_seconds)

    def _observe(
        self,
        values: dict[tuple[str, ...], _Summary],
        key: tuple[str, ...],
        duration_seconds: float,
    ) -> None:
        duration = duration_seconds if math.isfinite(duration_seconds) else 0.0
        duration = max(0.0, duration)
        with self._lock:
            summary = values.setdefault(key, _Summary())
            summary.count += 1
            summary.total += duration

    def render(self, gauges: dict[str, float] | None = None) -> str:
        """Render a consistent Prometheus text snapshot."""
        with self._lock:
            http = {key: _Summary(value.count, value.total) for key, value in self._http.items()}
            tasks = {key: _Summary(value.count, value.total) for key, value in self._tasks.items()}

        lines = [
            "# HELP cs2_http_requests_total Completed HTTP requests.",
            "# TYPE cs2_http_requests_total counter",
        ]
        for (method, route, status_code, outcome), summary in sorted(http.items()):
            labels = _labels(
                method=method,
                route=route,
                status_code=status_code,
                outcome=outcome,
            )
            lines.append(f"cs2_http_requests_total{labels} {summary.count}")

        lines.extend(
            (
                "# HELP cs2_http_request_duration_seconds HTTP request duration.",
                "# TYPE cs2_http_request_duration_seconds summary",
            )
        )
        for (method, route, status_code, outcome), summary in sorted(http.items()):
            labels = _labels(
                method=method,
                route=route,
                status_code=status_code,
                outcome=outcome,
            )
            lines.append(f"cs2_http_request_duration_seconds_count{labels} {summary.count}")
            lines.append(f"cs2_http_request_duration_seconds_sum{labels} {summary.total:.9f}")

        lines.extend(
            (
                "# HELP cs2_background_tasks_total Completed background tasks.",
                "# TYPE cs2_background_tasks_total counter",
            )
        )
        for (name, outcome), summary in sorted(tasks.items()):
            labels = _labels(name=name, outcome=outcome)
            lines.append(f"cs2_background_tasks_total{labels} {summary.count}")

        lines.extend(
            (
                "# HELP cs2_background_task_duration_seconds Background task duration.",
                "# TYPE cs2_background_task_duration_seconds summary",
            )
        )
        for (name, outcome), summary in sorted(tasks.items()):
            labels = _labels(name=name, outcome=outcome)
            lines.append(f"cs2_background_task_duration_seconds_count{labels} {summary.count}")
            lines.append(f"cs2_background_task_duration_seconds_sum{labels} {summary.total:.9f}")

        for name, value in sorted((gauges or {}).items()):
            lines.extend(
                (
                    f"# TYPE {name} gauge",
                    f"{name} {_number(value)}",
                )
            )
        lines.append("# EOF")
        return "\n".join(lines) + "\n"


async def render_runtime_metrics(
    container: AppContainer,
    registry: MetricsRegistry,
) -> str:
    """Add live resource gauges to the registry's cumulative measurements."""
    gauges: dict[str, float] = {}
    _database_gauges(container.database, gauges)
    _redis_gauges(container.redis, gauges)
    _task_gauges(container.task_supervisor, gauges)
    await _ssh_gauges(container.ssh_pool, gauges)
    return registry.render(gauges)


def _database_gauges(database: object, gauges: dict[str, float]) -> None:
    engine = getattr(database, "engine", None)
    pool = getattr(engine, "pool", None)
    if pool is None:
        sync_engine = getattr(engine, "sync_engine", None)
        pool = getattr(sync_engine, "pool", None)
    if pool is None:
        return
    _method_gauge(pool, "size", "cs2_db_pool_size", gauges)
    _method_gauge(pool, "checkedin", "cs2_db_pool_checked_in", gauges)
    _method_gauge(pool, "checkedout", "cs2_db_pool_checked_out", gauges)
    _method_gauge(pool, "overflow", "cs2_db_pool_overflow", gauges)


def _redis_gauges(redis: object, gauges: dict[str, float]) -> None:
    client = getattr(redis, "client", None)
    pool = getattr(client, "connection_pool", None)
    if pool is None:
        return
    in_use = getattr(pool, "_in_use_connections", ())
    available = getattr(pool, "_available_connections", ())
    gauges["cs2_redis_pool_in_use"] = float(len(in_use))
    gauges["cs2_redis_pool_available"] = float(len(available))
    max_connections = getattr(pool, "max_connections", None)
    if isinstance(max_connections, int | float):
        gauges["cs2_redis_pool_max_connections"] = float(max_connections)


def _task_gauges(supervisor: object, gauges: dict[str, float]) -> None:
    tasks = getattr(supervisor, "tasks", ())
    gauges["cs2_background_tasks_active"] = float(len(tasks))
    gauges["cs2_background_task_failures"] = float(getattr(supervisor, "failure_count", 0))


async def _ssh_gauges(pool: object | None, gauges: dict[str, float]) -> None:
    get_pool_stats = getattr(pool, "get_pool_stats", None)
    if get_pool_stats is None:
        return
    try:
        async with asyncio.timeout(0.5):
            stats = await get_pool_stats()
    except Exception:
        gauges["cs2_ssh_pool_metrics_available"] = 0.0
        return
    gauges["cs2_ssh_pool_metrics_available"] = 1.0
    for source, target in (
        ("total_connections", "cs2_ssh_pool_connections"),
        ("alive_connections", "cs2_ssh_pool_alive"),
        ("in_use_connections", "cs2_ssh_pool_in_use"),
        ("idle_connections", "cs2_ssh_pool_idle"),
        ("max_connections", "cs2_ssh_pool_max_connections"),
        ("available_capacity", "cs2_ssh_pool_available_capacity"),
    ):
        value = stats.get(source)
        if isinstance(value, int | float):
            gauges[target] = float(value)


def _method_gauge(
    target: object,
    method_name: str,
    metric_name: str,
    gauges: dict[str, float],
) -> None:
    method = getattr(target, method_name, None)
    if method is None:
        return
    try:
        value = method()
    except Exception:
        return
    if isinstance(value, int | float):
        gauges[metric_name] = float(value)


def _labels(**values: str) -> str:
    rendered = ",".join(f'{key}="{_escape_label(value)}"' for key, value in values.items())
    return "{" + rendered + "}"


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _number(value: float) -> str:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    return f"{value:.9g}"


__all__ = [
    "MetricsRegistry",
    "PROMETHEUS_CONTENT_TYPE",
    "render_runtime_metrics",
]
