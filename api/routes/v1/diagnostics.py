"""Administrator panel performance snapshot and 24-hour monitor for the console."""

from __future__ import annotations

import os
import platform
from typing import Annotated, Literal

import fastapi
from fastapi import APIRouter, HTTPException, Query, status

from api.contracts.v1.diagnostics import (
    PanelErrorListView,
    PanelMonitorView,
    PanelPerformanceSnapshot,
)
from api.dependencies import AdminUser
from api.metadata import APP_VERSION, BUILD_COMMIT, BUILD_TIME
from api.presenters.diagnostics import to_error_list, to_monitor_view
from modules.observability import is_enabled
from services.panel_metrics import capture_panel_performance
from services.panel_monitor import ErrorQuery, MonitorQuery, get_errors, get_monitor, history

router = APIRouter(prefix="/api/v1/diagnostics", tags=["v1-diagnostics"])
MonitorRange = Literal["15m", "1h", "6h", "24h"]


def _runtime_view() -> dict[str, object]:
    return {
        "version": APP_VERSION,
        "python": platform.python_version(),
        "fastapi": getattr(fastapi, "__version__", "") or "unknown",
        "git_sha": BUILD_COMMIT,
        "build_time": BUILD_TIME,
        "worker_pid": os.getpid(),
    }


@router.get("", response_model=PanelPerformanceSnapshot)
async def read_panel_performance(_current_user: AdminUser) -> PanelPerformanceSnapshot:
    """Return an in-process performance snapshot suitable for JSON export."""
    if not is_enabled():
        cached = history.cached_snapshot()
        if cached is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "MONITORING_DISABLED",
                    "message": "Panel monitoring is disabled",
                },
            )
        payload = dict(cached)
        payload["runtime"] = _runtime_view()
        return PanelPerformanceSnapshot.model_validate(payload)
    payload = await capture_panel_performance()
    payload["runtime"] = _runtime_view()
    return PanelPerformanceSnapshot.model_validate(payload)


@router.get("/monitor", response_model=PanelMonitorView)
async def read_panel_monitor(
    _current_user: AdminUser,
    range: Annotated[MonitorRange, Query()] = "1h",
    instance_id: str | None = None,
) -> PanelMonitorView:
    result = await get_monitor(MonitorQuery(range=range, instance_id=instance_id))
    return to_monitor_view(result, runtime=_runtime_view())


@router.get("/errors", response_model=PanelErrorListView)
async def read_panel_errors(
    _current_user: AdminUser,
    range: Annotated[MonitorRange, Query()] = "1h",
    instance_id: str | None = None,
    source: str | None = None,
    severity: str | None = None,
    route: str | None = None,
    operation_id: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> PanelErrorListView:
    result = await get_errors(
        ErrorQuery(
            range=range,
            instance_id=instance_id,
            source=source,
            severity=severity,
            route=route,
            operation_id=operation_id,
            cursor=cursor,
            limit=limit,
        )
    )
    return to_error_list(result)
