"""Administrator panel performance snapshot for analysis export."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from api.contracts.v1.identity import V1Model
from api.contracts.v1.overview import SshPoolView


class LatencyPercentiles(V1Model):
    """Nearest-rank latency percentiles in milliseconds."""

    p50: float = 0
    p95: float = 0
    p99: float = 0
    max: float = 0


class RouteLatencyView(V1Model):
    """One matched route in the current sample window."""

    method: str
    route: str
    count: int
    error_count: int = 0
    latency_ms: LatencyPercentiles


class RequestMetricsView(V1Model):
    """In-process HTTP timings. Health and this snapshot are excluded."""

    window_seconds: int
    sample_count: int
    requests_per_minute: float = 0
    status_2xx: int = 0
    status_4xx: int = 0
    status_5xx: int = 0
    error_rate: float = 0
    latency_ms: LatencyPercentiles
    slow_request_count: int = 0
    slow_request_threshold_ms: int = 500
    by_route: list[RouteLatencyView] = Field(default_factory=list)


class ProcessMetricsView(V1Model):
    """Panel process resource gauges. RSS may fall back to peak on some hosts."""

    pid: int
    uptime_seconds: float
    rss_bytes: int | None = None
    cpu_user_seconds: float = 0
    cpu_system_seconds: float = 0
    cpu_percent: float | None = None
    threads: int = 0
    asyncio_tasks: int | None = None
    event_loop_lag_ms: float | None = None


class DatabasePoolView(V1Model):
    """SQLAlchemy pool occupancy for this worker. None when the pool has no gauge."""

    pool_size: int
    max_overflow: int
    checked_out: int | None = None
    checked_in: int | None = None
    overflow: int | None = None


class RedisMetricsView(V1Model):
    """Redis ping latency. Disconnected is a field, not an HTTP error."""

    connected: bool
    ping_ms: float | None = None
    pool_max_connections: int = 0


class LimiterView(V1Model):
    """Telemetry limiter occupancy without host or server identities."""

    name: str
    global_limit: int
    per_key_limit: int
    active_keys: int = 0
    borrowers: int = 0


class OperationOccupancyView(V1Model):
    """In-memory operation hub occupancy for this worker."""

    running: int = 0
    queued: int = 0
    runners: int = 0
    subscribers: int = 0
    tracked: int = 0


class RuntimeMetricsView(V1Model):
    """Build identity already used by ``/health``. Unknown values stay placeholders."""

    version: str
    python: str
    fastapi: str
    git_sha: str
    build_time: str
    worker_pid: int


class PanelPerformanceSnapshot(V1Model):
    """Admin-only panel snapshot. Field order is the export priority order."""

    format: Literal["upkk-panel-performance"] = "upkk-panel-performance"
    version: int = 1
    captured_at: datetime
    priority: list[str]
    requests: RequestMetricsView
    process: ProcessMetricsView
    database: DatabasePoolView
    redis: RedisMetricsView
    ssh_pool: SshPoolView
    limiters: list[LimiterView] = Field(default_factory=list)
    operations: OperationOccupancyView
    runtime: RuntimeMetricsView


__all__ = [
    "DatabasePoolView",
    "LatencyPercentiles",
    "LimiterView",
    "OperationOccupancyView",
    "PanelPerformanceSnapshot",
    "ProcessMetricsView",
    "RedisMetricsView",
    "RequestMetricsView",
    "RouteLatencyView",
    "RuntimeMetricsView",
]
