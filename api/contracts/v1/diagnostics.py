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
    in_flight: int = 0
    status_401: int = 0
    status_403: int = 0
    status_404: int = 0
    status_409: int = 0
    status_422: int = 0
    status_429: int = 0
    unhandled_exceptions: int = 0
    stream_errors: int = 0
    cancellations: int = 0
    sse_count: int = 0
    http_count: int = 0
    latency_partial: bool = False


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
    event_loop_lag_p95_ms: float | None = None
    event_loop_lag_max_ms: float | None = None
    fd_open: int | None = None
    fd_limit: int | None = None


class DatabasePoolView(V1Model):
    """SQLAlchemy pool occupancy for this worker. None when the pool has no gauge."""

    pool_size: int
    max_overflow: int
    checked_out: int | None = None
    checked_in: int | None = None
    overflow: int | None = None
    capacity: int = 0
    invalidations: int = 0
    slow_executions: int = 0
    errors: int = 0
    execute_p95_ms: float | None = None


class RedisMetricsView(V1Model):
    """Redis ping latency. Disconnected is a field, not an HTTP error."""

    connected: bool
    ping_ms: float | None = None
    pool_max_connections: int = 0
    op_p95_ms: float | None = None
    failures: int = 0
    timeouts: int = 0
    monitor_ops: int = 0


class LimiterView(V1Model):
    """Telemetry limiter occupancy without host or server identities."""

    name: str
    global_limit: int
    per_key_limit: int
    active_keys: int = 0
    borrowers: int = 0
    executing: int = 0
    waiting: int = 0
    wait_p95_ms: float | None = None


class OperationOccupancyView(V1Model):
    """In-memory operation hub occupancy for this worker."""

    running: int = 0
    queued: int = 0
    runners: int = 0
    subscribers: int = 0
    tracked: int = 0
    oldest_queue_ms: float | None = None
    submitted: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0


class RuntimeMetricsView(V1Model):
    """Build identity already used by ``/health``. Unknown values stay placeholders."""

    version: str
    python: str
    fastapi: str
    git_sha: str
    build_time: str
    worker_pid: int


class OutboundHttpView(V1Model):
    """Shared HTTPHelper / download / AI transport totals for one group."""

    group: Literal["github", "download", "ai", "other"]
    calls: int = 0
    timeouts: int = 0
    network_errors: int = 0
    status_429: int = 0
    status_5xx: int = 0
    retries: int = 0
    p95_ms: float | None = None


class MonitorSelfView(V1Model):
    """Integrity of the collector itself."""

    collection_ms: float | None = None
    history_available: bool = True
    history_error: str | None = None
    dropped_error_details: int = 0
    truncated_summaries: int = 0


class MonitorFrameView(V1Model):
    file: str
    function: str
    line: int = 0


class MonitorSeriesPoint(V1Model):
    ts: datetime
    request_count: int = 0
    requests_per_minute: float = 0
    status_5xx: int = 0
    error_rate: float = 0
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    latency_p99_ms: float | None = None
    latency_max_ms: float | None = None
    latency_partial: bool = False
    cpu_percent: float | None = None
    rss_bytes: int | None = None
    rss_peak_bytes: int | None = None
    loop_lag_p95_ms: float | None = None
    db_checked_out: int | None = None
    db_capacity: int | None = None
    redis_ping_ms: float | None = None
    redis_connected: bool | None = None
    queue_running: int = 0
    queue_queued: int = 0
    oldest_queue_ms: float | None = None
    failed_tasks: int = 0
    unhandled_exceptions: int = 0
    fd_open: int | None = None
    fd_limit: int | None = None


class MonitorAlertView(V1Model):
    id: str
    severity: Literal["watch", "critical"]
    metric: str
    title: str
    detail: str
    guidance: str
    threshold: str
    value: float | None = None
    since: datetime | None = None


class MonitorInstanceView(V1Model):
    instance_id: str
    last_seen_at: datetime
    current: bool = False


class MonitorStatusView(V1Model):
    enabled: bool
    running: bool
    stale: bool
    history_available: bool
    history_error: str | None = None
    config_sync_error: str | None = None
    last_sample_at: datetime | None = None
    stopped_at: datetime | None = None
    instance_id: str
    sample_interval_seconds: int = 10
    dropped_error_details: int = 0
    truncated_summaries: int = 0
    collection_ms: float | None = None
    collecting: bool = False


class MonitorIntegrityView(V1Model):
    sample_interval_seconds: int = 10
    display_bucket_seconds: int = 30
    point_count: int = 0
    gap: bool = False
    partial_latency: bool = False


class MonitorErrorGroupView(V1Model):
    source: str | None = None
    error_code: str | None = None
    severity: str | None = None
    exception_type: str | None = None
    route: str | None = None
    count: int = 0
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    summary: str = ""
    request_id: str | None = None
    operation_id: str | None = None
    frames: list[MonitorFrameView] = Field(default_factory=list)


class MonitorErrorDetailView(V1Model):
    id: str
    ts: datetime
    source: str
    severity: str
    error_code: str
    exception_type: str
    summary: str
    truncated: bool = False
    route: str | None = None
    operation_id: str | None = None
    request_id: str | None = None
    frames: list[MonitorFrameView] = Field(default_factory=list)


class PanelMonitorView(V1Model):
    range: Literal["15m", "1h", "6h", "24h"]
    instance_id: str
    instances: list[MonitorInstanceView] = Field(default_factory=list)
    status: MonitorStatusView
    snapshot: PanelPerformanceSnapshot | None = None
    series: list[MonitorSeriesPoint] = Field(default_factory=list)
    alerts: list[MonitorAlertView] = Field(default_factory=list)
    error_groups: list[MonitorErrorGroupView] = Field(default_factory=list)
    integrity: MonitorIntegrityView


class PanelErrorListView(V1Model):
    items: list[MonitorErrorDetailView] = Field(default_factory=list)
    next_cursor: str | None = None
    dropped: int = 0
    truncated: bool = False


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
    outbound_http: list[OutboundHttpView] = Field(default_factory=list)
    monitor: MonitorSelfView | None = None


__all__ = [
    "DatabasePoolView",
    "LatencyPercentiles",
    "LimiterView",
    "MonitorAlertView",
    "MonitorErrorDetailView",
    "MonitorErrorGroupView",
    "MonitorFrameView",
    "MonitorInstanceView",
    "MonitorIntegrityView",
    "MonitorSelfView",
    "MonitorSeriesPoint",
    "MonitorStatusView",
    "OperationOccupancyView",
    "OutboundHttpView",
    "PanelErrorListView",
    "PanelMonitorView",
    "PanelPerformanceSnapshot",
    "ProcessMetricsView",
    "RedisMetricsView",
    "RequestMetricsView",
    "RouteLatencyView",
    "RuntimeMetricsView",
]
