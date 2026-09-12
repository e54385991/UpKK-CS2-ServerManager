"""Bounded in-memory error details. No I/O."""

from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass
from time import time
from typing import Any

from .context import current_operation_id, current_request_id
from .control import generation as current_switch_generation
from .control import is_enabled
from .frames import capture_frames
from .redact import SUMMARY_LIMIT, redact_summary

MEMORY_ERROR_LIMIT = 1000
MEMORY_ERROR_SECONDS = 900

SOURCES = frozenset({"request", "database", "redis", "ssh", "http", "task", "log"})
SEVERITIES = frozenset({"warning", "error", "critical"})


@dataclass(frozen=True)
class ErrorEvent:
    id: str
    ts: float
    generation: int
    source: str
    severity: str
    error_code: str
    exception_type: str
    summary: str
    truncated: bool
    route: str | None
    operation_id: str | None
    request_id: str | None
    frames: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "generation": self.generation,
            "source": self.source,
            "severity": self.severity,
            "error_code": self.error_code,
            "exception_type": self.exception_type,
            "summary": self.summary,
            "truncated": self.truncated,
            "route": self.route,
            "operation_id": self.operation_id,
            "request_id": self.request_id,
            "frames": list(self.frames),
        }


_lock = threading.Lock()
_events: deque[ErrorEvent] = deque(maxlen=MEMORY_ERROR_LIMIT)
_dropped = 0
_truncated = 0


def record_error(
    *,
    source: str,
    summary: str,
    error_code: str = "error",
    severity: str = "error",
    exception: BaseException | None = None,
    exception_type: str | None = None,
    route: str | None = None,
    operation_id: str | None = None,
    request_id: str | None = None,
    generation: int | None = None,
    frames: list[dict[str, Any]] | None = None,
) -> None:
    if not is_enabled():
        return
    gen = current_switch_generation() if generation is None else generation
    if gen != current_switch_generation():
        return
    source_key = source if source in SOURCES else "log"
    severity_key = severity if severity in SEVERITIES else "error"
    safe, truncated = redact_summary(summary, limit=SUMMARY_LIMIT)
    kind = exception_type
    if kind is None and exception is not None:
        kind = type(exception).__name__
    stored_frames = frames if frames is not None else capture_frames(exception)
    event = ErrorEvent(
        id=uuid.uuid4().hex,
        ts=time(),
        generation=gen,
        source=source_key,
        severity=severity_key,
        error_code=(error_code or "error")[:64],
        exception_type=(kind or "Error")[:128],
        summary=safe,
        truncated=truncated,
        route=route,
        operation_id=operation_id or current_operation_id(),
        request_id=request_id or current_request_id(),
        frames=tuple(stored_frames[:8]),
    )
    global _dropped, _truncated
    with _lock:
        if len(_events) >= MEMORY_ERROR_LIMIT:
            _dropped += 1
        if truncated:
            _truncated += 1
        _events.append(event)


def drain_errors(*, since: float | None = None) -> list[dict[str, Any]]:
    cutoff = time() - MEMORY_ERROR_SECONDS if since is None else since
    with _lock:
        items = [event.as_dict() for event in _events if event.ts >= cutoff]
    return items


def list_errors(
    *,
    start_ts: float,
    end_ts: float,
    source: str | None = None,
    severity: str | None = None,
    route: str | None = None,
    operation_id: str | None = None,
    cursor_ts: float | None = None,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    with _lock:
        dropped = _dropped
        items = [
            event.as_dict()
            for event in _events
            if start_ts <= event.ts <= end_ts
            and (source is None or event.source == source)
            and (severity is None or event.severity == severity)
            and (route is None or event.route == route)
            and (operation_id is None or event.operation_id == operation_id)
            and (cursor_ts is None or event.ts < cursor_ts)
        ]
    items.sort(key=lambda item: item["ts"], reverse=True)
    return items[: max(1, min(limit, 100))], dropped


def error_stats() -> dict[str, int]:
    with _lock:
        return {
            "stored": len(_events),
            "dropped": _dropped,
            "truncated": _truncated,
        }


def clear_errors() -> None:
    global _dropped, _truncated
    with _lock:
        _events.clear()
        _dropped = 0
        _truncated = 0
