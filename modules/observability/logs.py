"""Bounded logging handler that never performs I/O of its own."""

from __future__ import annotations

import logging
import queue
from typing import Any

from .control import generation as current_switch_generation
from .control import is_enabled
from .events import record_error

HANDLER_NAME = "panel-monitor-logs"
QUEUE_LIMIT = 256
SKIP_LOGGERS = (
    "modules.observability",
    "services.panel_monitor",
    "services.panel_metrics",
)

_queue: queue.Queue[dict[str, Any]] | None = None
_dropped = 0


class MonitorLogHandler(logging.Handler):
    """Enqueue ERROR/CRITICAL records for the collector to persist."""

    def emit(self, record: logging.LogRecord) -> None:
        global _dropped
        if not is_enabled():
            return
        if record.levelno < logging.ERROR:
            return
        if record.name.startswith(SKIP_LOGGERS):
            return
        payload = {
            "generation": current_switch_generation(),
            "severity": "critical" if record.levelno >= logging.CRITICAL else "error",
            "logger": record.name,
            "message": record.getMessage(),
            "exception_type": (
                record.exc_info[0].__name__
                if record.exc_info and record.exc_info[0] is not None
                else "LogRecord"
            ),
        }
        target = _queue
        if target is None:
            return
        try:
            target.put_nowait(payload)
        except queue.Full:
            _dropped += 1


def install_log_handler() -> None:
    global _queue
    root = logging.getLogger()
    if any(handler.get_name() == HANDLER_NAME for handler in root.handlers):
        if _queue is None:
            _queue = queue.Queue(maxsize=QUEUE_LIMIT)
        return
    _queue = queue.Queue(maxsize=QUEUE_LIMIT)
    handler = MonitorLogHandler(level=logging.ERROR)
    handler.set_name(HANDLER_NAME)
    root.addHandler(handler)


def uninstall_log_handler() -> None:
    global _queue
    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler.get_name() == HANDLER_NAME:
            root.removeHandler(handler)
            handler.close()
    drain_log_queue()
    _queue = None


def drain_log_queue() -> int:
    """Move queued log records into the error ring."""
    global _dropped
    target = _queue
    if target is None:
        return _dropped
    while True:
        try:
            payload = target.get_nowait()
        except queue.Empty:
            break
        record_error(
            source="log",
            summary=str(payload.get("message") or ""),
            error_code="log",
            severity=str(payload.get("severity") or "error"),
            exception_type=str(payload.get("exception_type") or "LogRecord"),
            generation=int(payload.get("generation") or 0),
        )
    dropped = _dropped
    _dropped = 0
    return dropped
