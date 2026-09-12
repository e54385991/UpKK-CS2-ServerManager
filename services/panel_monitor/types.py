"""Transport-independent results for panel monitoring queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

MonitorRange = Literal["15m", "1h", "6h", "24h"]

RANGE_SECONDS: dict[str, int] = {
    "15m": 900,
    "1h": 3600,
    "6h": 21600,
    "24h": 86400,
}
DISPLAY_BUCKET_SECONDS: dict[str, int] = {
    "15m": 10,
    "1h": 30,
    "6h": 60,
    "24h": 300,
}


@dataclass(frozen=True)
class MonitorInstance:
    instance_id: str
    last_seen_ts: float
    current: bool


@dataclass
class MonitorStatus:
    enabled: bool
    running: bool
    stale: bool
    history_available: bool
    history_error: str | None
    config_sync_error: str | None
    last_sample_at: float | None
    stopped_at: float | None
    instance_id: str
    sample_interval_seconds: int = 10
    dropped_error_details: int = 0
    truncated_summaries: int = 0
    collection_ms: float | None = None
    collecting: bool = False


@dataclass
class MonitorAlert:
    id: str
    severity: Literal["watch", "critical"]
    metric: str
    title: str
    detail: str
    guidance: str
    threshold: str
    value: float | None = None
    since: float | None = None


@dataclass
class MonitorResult:
    range: str
    instance_id: str
    instances: list[MonitorInstance]
    status: MonitorStatus
    snapshot: dict[str, Any] | None
    series: list[dict[str, Any]]
    alerts: list[MonitorAlert]
    error_groups: list[dict[str, Any]]
    integrity: dict[str, Any]


@dataclass
class ErrorListResult:
    items: list[dict[str, Any]]
    next_cursor: str | None
    dropped: int
    truncated: bool = False


@dataclass
class MonitorQuery:
    range: str = "1h"
    instance_id: str | None = None


@dataclass
class ErrorQuery:
    range: str = "1h"
    instance_id: str | None = None
    source: str | None = None
    severity: str | None = None
    route: str | None = None
    operation_id: str | None = None
    cursor: str | None = None
    limit: int = 50
