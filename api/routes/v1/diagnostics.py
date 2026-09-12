"""Administrator panel performance snapshot for the Next.js console."""

from __future__ import annotations

import os
import platform

import fastapi
from fastapi import APIRouter

from api.contracts.v1.diagnostics import PanelPerformanceSnapshot
from api.dependencies import AdminUser
from api.metadata import APP_VERSION, BUILD_COMMIT, BUILD_TIME
from services.panel_metrics import capture_panel_performance

router = APIRouter(prefix="/api/v1/diagnostics", tags=["v1-diagnostics"])


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
    payload = await capture_panel_performance()
    payload["runtime"] = _runtime_view()
    return PanelPerformanceSnapshot.model_validate(payload)
