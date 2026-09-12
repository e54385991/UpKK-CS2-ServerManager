"""Default alert rules for the panel monitor dashboard."""

from __future__ import annotations

from typing import Any

from .types import MonitorAlert

P95_WATCH_MS = 500
P95_CRITICAL_MS = 1000
P95_MIN_REQUESTS = 20
ERROR_RATE_CRITICAL = 0.05
ERROR_RATE_MIN_REQUESTS = 20
LOOP_WATCH_MS = 50
LOOP_CRITICAL_MS = 200
DB_WATCH_RATIO = 0.80
FD_WATCH_RATIO = 0.80
FD_CRITICAL_RATIO = 0.95
HYSTERESIS = 3


def evaluate_alerts(
    points: list[dict[str, Any]],
    *,
    enabled: bool,
    stale: bool,
    redis_connected: bool | None,
    unhandled: int,
    failed_tasks: int,
    log_errors: int,
) -> list[MonitorAlert]:
    alerts: list[MonitorAlert] = []
    recent = points[-HYSTERESIS:] if points else []
    if not enabled:
        return alerts
    if stale:
        alerts.append(
            MonitorAlert(
                id="stale",
                severity="watch",
                metric="sample_age",
                title="Monitoring data is stale",
                detail="The latest sample is more than 30 seconds old.",
                guidance="Confirm the panel process is running and the collector has not stalled.",
                threshold=">30s since last sample",
            )
        )
    p95_alert = _hysteresis_latency(recent)
    if p95_alert is not None:
        alerts.append(p95_alert)
    error_alert = _error_rate_alert(recent)
    if error_alert is not None:
        alerts.append(error_alert)
    loop_alert = _hysteresis_loop(recent)
    if loop_alert is not None:
        alerts.append(loop_alert)
    db_alert = _hysteresis_db(recent)
    if db_alert is not None:
        alerts.append(db_alert)
    fd_alert = _hysteresis_fd(recent)
    if fd_alert is not None:
        alerts.append(fd_alert)
    if redis_connected is False:
        alerts.append(
            MonitorAlert(
                id="redis",
                severity="critical",
                metric="redis",
                title="Redis is disconnected",
                detail="The panel could not ping Redis.",
                guidance="Check Redis connectivity, credentials, and the configured host/port.",
                threshold="connection failed",
            )
        )
    if unhandled > 0:
        alerts.append(
            MonitorAlert(
                id="unhandled",
                severity="critical",
                metric="unhandled_exceptions",
                title="Unhandled request exceptions",
                detail=f"{unhandled} unhandled exception(s) in the latest window.",
                guidance="Open the error center and inspect the associated request ID and frames.",
                threshold="any new unhandled exception",
                value=float(unhandled),
            )
        )
    if failed_tasks > 0:
        alerts.append(
            MonitorAlert(
                id="failed_tasks",
                severity="critical",
                metric="failed_tasks",
                title="Background tasks failed",
                detail=f"{failed_tasks} task(s) ended in failure.",
                guidance="Open the activity tray or the matching operation log for the failed job.",
                threshold="any new failed task",
                value=float(failed_tasks),
            )
        )
    if log_errors > 0:
        alerts.append(
            MonitorAlert(
                id="log_errors",
                severity="watch",
                metric="logs",
                title="Background ERROR logs",
                detail=f"{log_errors} ERROR/CRITICAL log record(s) in the latest window.",
                guidance="Review the error center summaries; they are redacted and do not include bodies.",
                threshold="any ERROR/CRITICAL log",
                value=float(log_errors),
            )
        )
    return alerts


def _hysteresis_latency(recent: list[dict[str, Any]]) -> MonitorAlert | None:
    qualifying = [
        point
        for point in recent
        if int(point.get("req") or 0) >= P95_MIN_REQUESTS and point.get("p95") is not None
    ]
    if len(qualifying) < HYSTERESIS:
        return None
    values = [float(point["p95"]) for point in qualifying]
    if all(value >= P95_CRITICAL_MS for value in values):
        return _latency_alert("critical", max(values), f">={P95_CRITICAL_MS} ms")
    if all(value >= P95_WATCH_MS for value in values):
        return _latency_alert("watch", max(values), f">={P95_WATCH_MS} ms")
    return None


def _latency_alert(severity: str, value: float, threshold: str) -> MonitorAlert:
    return MonitorAlert(
        id="request_p95",
        severity="critical" if severity == "critical" else "watch",
        metric="request_p95",
        title="Request p95 is elevated",
        detail=f"Request p95 reached {value:.0f} ms across {HYSTERESIS} samples with at least {P95_MIN_REQUESTS} requests each.",
        guidance="Inspect slow routes, event-loop lag, and database/Redis latency on the same timeline.",
        threshold=threshold,
        value=value,
    )


def _error_rate_alert(recent: list[dict[str, Any]]) -> MonitorAlert | None:
    if not recent:
        return None
    latest = recent[-1]
    requests = int(latest.get("req") or 0)
    status_5xx = int(latest.get("s5") or 0)
    if status_5xx <= 0:
        return None
    rate = status_5xx / requests if requests else 0.0
    if requests >= ERROR_RATE_MIN_REQUESTS and rate >= ERROR_RATE_CRITICAL:
        return MonitorAlert(
            id="http_5xx",
            severity="critical",
            metric="http_5xx",
            title="5xx error rate is high",
            detail=f"{status_5xx} of {requests} requests returned 5xx ({rate:.1%}).",
            guidance="Filter the error center by request source and inspect the slow-route table.",
            threshold=f">={ERROR_RATE_CRITICAL:.0%} with >={ERROR_RATE_MIN_REQUESTS} requests",
            value=rate,
        )
    return MonitorAlert(
        id="http_5xx",
        severity="watch",
        metric="http_5xx",
        title="5xx responses observed",
        detail=f"{status_5xx} 5xx response(s) in the latest sample.",
        guidance="A small number of 5xx responses is not a systemic fault; check whether they cluster on one route.",
        threshold="any 5xx",
        value=float(status_5xx),
    )


def _hysteresis_loop(recent: list[dict[str, Any]]) -> MonitorAlert | None:
    values = [float(point["lag"]) for point in recent if point.get("lag") is not None]
    if len(values) < HYSTERESIS:
        return None
    if all(value >= LOOP_CRITICAL_MS for value in values):
        return MonitorAlert(
            id="loop_lag",
            severity="critical",
            metric="loop_lag",
            title="Event loop lag is severe",
            detail=f"Event-loop lag stayed at {max(values):.0f} ms for {HYSTERESIS} samples.",
            guidance="Look for blocking I/O on the event loop and long CPU stretches in this worker.",
            threshold=f">={LOOP_CRITICAL_MS} ms",
            value=max(values),
        )
    if all(value >= LOOP_WATCH_MS for value in values):
        return MonitorAlert(
            id="loop_lag",
            severity="watch",
            metric="loop_lag",
            title="Event loop lag is elevated",
            detail=f"Event-loop lag stayed at {max(values):.0f} ms for {HYSTERESIS} samples.",
            guidance="Check slow requests and SSH/database work that may be blocking the loop.",
            threshold=f">={LOOP_WATCH_MS} ms",
            value=max(values),
        )
    return None


def _hysteresis_db(recent: list[dict[str, Any]]) -> MonitorAlert | None:
    ratios: list[float] = []
    at_capacity = True
    for point in recent:
        used = point.get("dbx")
        capacity = point.get("dbc")
        if used is None or not capacity:
            return None
        ratio = float(used) / float(capacity)
        ratios.append(ratio)
        if float(used) < float(capacity):
            at_capacity = False
    if len(ratios) < HYSTERESIS:
        return None
    if at_capacity:
        return MonitorAlert(
            id="db_pool",
            severity="critical",
            metric="db_pool",
            title="Database pool is exhausted",
            detail="Checked-out connections reached pool capacity (including overflow) for three samples.",
            guidance="Reduce concurrent database work or raise pool_size / max_overflow after reviewing occupancy.",
            threshold="checked_out >= capacity",
            value=1.0,
        )
    if all(ratio >= DB_WATCH_RATIO for ratio in ratios):
        return MonitorAlert(
            id="db_pool",
            severity="watch",
            metric="db_pool",
            title="Database pool occupancy is high",
            detail=f"Occupancy stayed at {max(ratios):.0%} of capacity for {HYSTERESIS} samples.",
            guidance="Watch overflow connections and slow SQL; capacity includes max_overflow.",
            threshold=f">={DB_WATCH_RATIO:.0%} of pool_size+max_overflow",
            value=max(ratios),
        )
    return None


def _hysteresis_fd(recent: list[dict[str, Any]]) -> MonitorAlert | None:
    ratios: list[float] = []
    for point in recent:
        used = point.get("fd")
        limit = point.get("fdmax")
        if used is None or not limit:
            return None
        ratios.append(float(used) / float(limit))
    if len(ratios) < HYSTERESIS:
        return None
    if all(ratio >= FD_CRITICAL_RATIO for ratio in ratios):
        return MonitorAlert(
            id="fd",
            severity="critical",
            metric="fd",
            title="File descriptor usage is critical",
            detail=f"Open file descriptors stayed at {max(ratios):.0%} of the process limit.",
            guidance="Inspect SSH, HTTP, and database connection leaks; restarting the worker is a last resort.",
            threshold=f">={FD_CRITICAL_RATIO:.0%}",
            value=max(ratios),
        )
    if all(ratio >= FD_WATCH_RATIO for ratio in ratios):
        return MonitorAlert(
            id="fd",
            severity="watch",
            metric="fd",
            title="File descriptor usage is high",
            detail=f"Open file descriptors stayed at {max(ratios):.0%} of the process limit.",
            guidance="Watch connection pools and SSE subscribers if this keeps climbing.",
            threshold=f">={FD_WATCH_RATIO:.0%}",
            value=max(ratios),
        )
    return None
