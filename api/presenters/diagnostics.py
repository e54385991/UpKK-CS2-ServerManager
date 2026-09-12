"""Map panel-monitor service results to versioned response DTOs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast

from api.contracts.v1.diagnostics import (
    MonitorAlertView,
    MonitorErrorDetailView,
    MonitorErrorGroupView,
    MonitorFrameView,
    MonitorInstanceView,
    MonitorIntegrityView,
    MonitorSeriesPoint,
    MonitorStatusView,
    PanelErrorListView,
    PanelMonitorView,
    PanelPerformanceSnapshot,
)
from services.panel_monitor.types import ErrorListResult, MonitorResult

RangeName = Literal["15m", "1h", "6h", "24h"]


def to_monitor_view(result: MonitorResult, *, runtime: dict[str, object]) -> PanelMonitorView:
    snapshot = None
    if result.snapshot is not None:
        payload = dict(result.snapshot)
        payload.setdefault("runtime", runtime)
        snapshot = PanelPerformanceSnapshot.model_validate(payload)
    return PanelMonitorView(
        range=cast(RangeName, result.range),
        instance_id=result.instance_id,
        instances=[
            MonitorInstanceView(
                instance_id=item.instance_id,
                last_seen_at=_ts(item.last_seen_ts),
                current=item.current,
            )
            for item in result.instances
        ],
        status=MonitorStatusView(
            enabled=result.status.enabled,
            running=result.status.running,
            stale=result.status.stale,
            history_available=result.status.history_available,
            history_error=result.status.history_error,
            config_sync_error=result.status.config_sync_error,
            last_sample_at=_optional_ts(result.status.last_sample_at),
            stopped_at=_optional_ts(result.status.stopped_at),
            instance_id=result.status.instance_id,
            sample_interval_seconds=result.status.sample_interval_seconds,
            dropped_error_details=result.status.dropped_error_details,
            truncated_summaries=result.status.truncated_summaries,
            collection_ms=result.status.collection_ms,
            collecting=result.status.collecting,
        ),
        snapshot=snapshot,
        series=[_series_point(point) for point in result.series],
        alerts=[
            MonitorAlertView(
                id=alert.id,
                severity=alert.severity,
                metric=alert.metric,
                title=alert.title,
                detail=alert.detail,
                guidance=alert.guidance,
                threshold=alert.threshold,
                value=alert.value,
                since=_optional_ts(alert.since),
            )
            for alert in result.alerts
        ],
        error_groups=[_error_group(group) for group in result.error_groups],
        integrity=MonitorIntegrityView(
            sample_interval_seconds=int(result.integrity.get("sample_interval_seconds") or 10),
            display_bucket_seconds=int(result.integrity.get("display_bucket_seconds") or 30),
            point_count=int(result.integrity.get("point_count") or 0),
            gap=bool(result.integrity.get("gap")),
            partial_latency=bool(result.integrity.get("partial_latency")),
        ),
    )


def to_error_list(result: ErrorListResult) -> PanelErrorListView:
    return PanelErrorListView(
        items=[_error_detail(item) for item in result.items],
        next_cursor=result.next_cursor,
        dropped=result.dropped,
        truncated=result.truncated,
    )


def _series_point(point: dict[str, Any]) -> MonitorSeriesPoint:
    return MonitorSeriesPoint(
        ts=_ts(point.get("ts") or 0),
        request_count=int(point.get("request_count") or 0),
        requests_per_minute=float(point.get("requests_per_minute") or 0),
        status_5xx=int(point.get("status_5xx") or 0),
        error_rate=float(point.get("error_rate") or 0),
        latency_p50_ms=_optional_float(point.get("latency_p50_ms")),
        latency_p95_ms=_optional_float(point.get("latency_p95_ms")),
        latency_p99_ms=_optional_float(point.get("latency_p99_ms")),
        latency_max_ms=_optional_float(point.get("latency_max_ms")),
        latency_partial=bool(point.get("latency_partial")),
        cpu_percent=_optional_float(point.get("cpu_percent")),
        rss_bytes=_optional_int(point.get("rss_bytes")),
        rss_peak_bytes=_optional_int(point.get("rss_peak_bytes")),
        loop_lag_p95_ms=_optional_float(point.get("loop_lag_p95_ms")),
        db_checked_out=_optional_int(point.get("db_checked_out")),
        db_capacity=_optional_int(point.get("db_capacity")),
        redis_ping_ms=_optional_float(point.get("redis_ping_ms")),
        redis_connected=point.get("redis_connected")
        if isinstance(point.get("redis_connected"), bool)
        else None,
        queue_running=int(point.get("queue_running") or 0),
        queue_queued=int(point.get("queue_queued") or 0),
        oldest_queue_ms=_optional_float(point.get("oldest_queue_ms")),
        failed_tasks=int(point.get("failed_tasks") or 0),
        unhandled_exceptions=int(point.get("unhandled_exceptions") or 0),
        fd_open=_optional_int(point.get("fd_open")),
        fd_limit=_optional_int(point.get("fd_limit")),
    )


def _error_group(group: dict[str, Any]) -> MonitorErrorGroupView:
    return MonitorErrorGroupView(
        source=_optional_str(group.get("source")),
        error_code=_optional_str(group.get("error_code")),
        severity=_optional_str(group.get("severity")),
        exception_type=_optional_str(group.get("exception_type")),
        route=_optional_str(group.get("route")),
        count=int(group.get("count") or 0),
        first_ts=_optional_ts(group.get("first_ts")),
        last_ts=_optional_ts(group.get("last_ts")),
        summary=str(group.get("summary") or ""),
        request_id=_optional_str(group.get("request_id")),
        operation_id=_optional_str(group.get("operation_id")),
        frames=_frames(group.get("frames")),
    )


def _error_detail(item: dict[str, Any]) -> MonitorErrorDetailView:
    return MonitorErrorDetailView(
        id=str(item.get("id") or ""),
        ts=_ts(item.get("ts") or 0),
        source=str(item.get("source") or "log"),
        severity=str(item.get("severity") or "error"),
        error_code=str(item.get("error_code") or "error"),
        exception_type=str(item.get("exception_type") or "Error"),
        summary=str(item.get("summary") or ""),
        truncated=bool(item.get("truncated")),
        route=_optional_str(item.get("route")),
        operation_id=_optional_str(item.get("operation_id")),
        request_id=_optional_str(item.get("request_id")),
        frames=_frames(item.get("frames")),
    )


def _frames(value: object) -> list[MonitorFrameView]:
    if not isinstance(value, list):
        return []
    frames: list[MonitorFrameView] = []
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        frames.append(
            MonitorFrameView(
                file=str(item.get("file") or ""),
                function=str(item.get("function") or ""),
                line=int(item.get("line") or 0),
            )
        )
    return frames


def _ts(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    numeric = _optional_float(value)
    if numeric is None:
        return datetime.now(UTC)
    try:
        return datetime.fromtimestamp(numeric, tz=UTC)
    except OSError:
        return datetime.now(UTC)


def _optional_ts(value: object) -> datetime | None:
    if value is None:
        return None
    return _ts(value)


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float | str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float | str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
