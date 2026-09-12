"""Assemble monitor and error-list results for the diagnostics routes."""

from __future__ import annotations

from collections import defaultdict
from time import time
from typing import Any

from modules.observability import (
    error_stats,
    instance_id,
    is_enabled,
    started_at,
    stopped_at,
    sync_error,
)
from services.panel_monitor.aggregation import display_series, range_start
from services.panel_monitor.alerts import evaluate_alerts
from services.panel_monitor.history import history
from services.panel_monitor.runtime import sample_is_stale
from services.panel_monitor.types import (
    DISPLAY_BUCKET_SECONDS,
    RANGE_SECONDS,
    ErrorListResult,
    ErrorQuery,
    MonitorInstance,
    MonitorQuery,
    MonitorResult,
    MonitorStatus,
)


async def get_monitor(query: MonitorQuery) -> MonitorResult:
    range_key = query.range if query.range in RANGE_SECONDS else "1h"
    now = time()
    current = instance_id()
    selected = query.instance_id or current
    instances = [
        MonitorInstance(instance_id=item_id, last_seen_ts=seen, current=item_id == current)
        for item_id, seen in await history.list_instances()
    ]
    start_ts = range_start(now, range_key)
    points = await history.load_points(instance=selected, start_ts=start_ts, end_ts=now)
    errors = await history.load_errors(instance=selected, start_ts=start_ts, end_ts=now)
    snapshot = await history.load_snapshot(selected)
    if snapshot is None and selected == current and is_enabled():
        from services.panel_metrics import capture_panel_performance

        snapshot = await capture_panel_performance()
    redis_connected = None
    redis_view = snapshot.get("redis") if snapshot else None
    if isinstance(redis_view, dict):
        connected = redis_view.get("connected")
        redis_connected = connected if isinstance(connected, bool) else None
    latest_counts = _latest_counts(points)
    stale = selected == current and sample_is_stale(now)
    status = MonitorStatus(
        enabled=is_enabled(),
        running=is_enabled() and selected == current,
        stale=stale,
        history_available=history.history_available,
        history_error=history.history_error,
        config_sync_error=sync_error(),
        last_sample_at=history.last_sample_at if selected == current else _last_point_ts(points),
        stopped_at=stopped_at() if not is_enabled() else None,
        instance_id=selected,
        dropped_error_details=history.dropped_errors + error_stats()["dropped"],
        truncated_summaries=history.truncated_summaries + error_stats()["truncated"],
        collection_ms=history.last_collection_ms if selected == current else None,
        collecting=is_enabled() and selected == current and started_at() is not None,
    )
    alerts = evaluate_alerts(
        points[-12:],
        enabled=is_enabled() and selected == current,
        stale=stale,
        redis_connected=redis_connected,
        unhandled=latest_counts["unhandled"],
        failed_tasks=latest_counts["failed_tasks"],
        log_errors=_count_source(errors, "log"),
    )
    return MonitorResult(
        range=range_key,
        instance_id=selected,
        instances=instances,
        status=status,
        snapshot=snapshot,
        series=display_series(points, range_key),
        alerts=alerts,
        error_groups=_group_errors(errors),
        integrity={
            "sample_interval_seconds": 10,
            "display_bucket_seconds": DISPLAY_BUCKET_SECONDS[range_key],
            "point_count": len(points),
            "gap": not history.history_available,
            "partial_latency": any(bool(point.get("part")) for point in points),
        },
    )


async def get_errors(query: ErrorQuery) -> ErrorListResult:
    range_key = query.range if query.range in RANGE_SECONDS else "1h"
    now = time()
    selected = query.instance_id or instance_id()
    start_ts = range_start(now, range_key)
    items = await history.load_errors(instance=selected, start_ts=start_ts, end_ts=now)
    cursor_ts = _parse_cursor(query.cursor)
    filtered = [
        item
        for item in items
        if (query.source is None or item.get("source") == query.source)
        and (query.severity is None or item.get("severity") == query.severity)
        and (query.route is None or item.get("route") == query.route)
        and (query.operation_id is None or item.get("operation_id") == query.operation_id)
        and (cursor_ts is None or float(item.get("ts") or 0) < cursor_ts)
    ]
    filtered.sort(key=lambda item: float(item.get("ts") or 0), reverse=True)
    limit = max(1, min(query.limit, 100))
    page = filtered[:limit]
    next_cursor = None
    if len(filtered) > limit and page:
        next_cursor = str(page[-1].get("ts"))
    return ErrorListResult(
        items=page,
        next_cursor=next_cursor,
        dropped=history.dropped_errors + error_stats()["dropped"],
        truncated=any(bool(item.get("truncated")) for item in page),
    )


def _latest_counts(points: list[dict[str, Any]]) -> dict[str, int]:
    if not points:
        return {"unhandled": 0, "failed_tasks": 0}
    latest = points[-1]
    return {
        "unhandled": int(latest.get("unh") or 0),
        "failed_tasks": int(latest.get("tfail") or 0),
    }


def _last_point_ts(points: list[dict[str, Any]]) -> float | None:
    if not points:
        return None
    return float(points[-1].get("t") or 0)


def _count_source(errors: list[dict[str, Any]], source: str) -> int:
    return sum(1 for item in errors if item.get("source") == source)


def _group_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "first_ts": None,
            "last_ts": None,
            "summary": "",
            "frames": [],
            "request_id": None,
            "operation_id": None,
        }
    )
    for event in errors:
        frames = event.get("frames") or []
        location = ""
        if frames:
            first = frames[0]
            location = f"{first.get('file')}:{first.get('line')}"
        key = (
            str(event.get("source") or ""),
            str(event.get("error_code") or ""),
            str(event.get("route") or ""),
            location,
        )
        group = groups[key]
        group["count"] += 1
        ts = float(event.get("ts") or 0)
        group["first_ts"] = ts if group["first_ts"] is None else min(group["first_ts"], ts)
        group["last_ts"] = ts if group["last_ts"] is None else max(group["last_ts"], ts)
        group["summary"] = event.get("summary") or group["summary"]
        group["frames"] = frames
        group["source"] = event.get("source")
        group["error_code"] = event.get("error_code")
        group["severity"] = event.get("severity")
        group["exception_type"] = event.get("exception_type")
        group["route"] = event.get("route")
        group["request_id"] = event.get("request_id") or group["request_id"]
        group["operation_id"] = event.get("operation_id") or group["operation_id"]
    ranked = sorted(groups.values(), key=lambda item: item["last_ts"] or 0, reverse=True)
    return ranked[:20]


def _parse_cursor(cursor: str | None) -> float | None:
    if not cursor:
        return None
    try:
        return float(cursor)
    except ValueError:
        return None
