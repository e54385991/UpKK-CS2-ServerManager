"""Environment-aware logging configuration for CS2 Server Manager."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from cs2_manager.core.observability import current_request_id

LOG_DIR = "logs"
LOG_FILE = "cs2_manager.log"
MAX_LOG_SIZE = 10 * 1024 * 1024
BACKUP_COUNT = 10
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - [request_id=%(request_id)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_STRUCTURED_FIELDS = (
    "duration_ms",
    "http_method",
    "http_path",
    "http_route",
    "http_status_code",
    "task_name",
)


class RequestContextFilter(logging.Filter):
    """Attach the async request ID to every application and library record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = current_request_id()
        return True


class JSONFormatter(logging.Formatter):
    """Emit one machine-readable JSON object per stdout line."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat(
            timespec="milliseconds"
        )
        payload: dict[str, object] = {
            "timestamp": timestamp.replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        for field in _STRUCTURED_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _get_log_level(level_str: str) -> int:
    """Convert a level name to a logging constant, defaulting to INFO."""
    if not level_str or not isinstance(level_str, str):
        return logging.INFO
    return {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }.get(level_str.upper(), logging.INFO)


def setup_logging(
    level: int = logging.INFO,
    asyncssh_level: str | None = None,
    *,
    environment: str = "development",
) -> None:
    """Configure JSON stdout in production and readable logs in development."""
    production = environment.lower() == "production"
    context_filter = RequestContextFilter()
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.addFilter(context_filter)
    if production:
        console_handler.setFormatter(JSONFormatter())
        handlers: list[logging.Handler] = [console_handler]
    else:
        formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
        console_handler.setFormatter(formatter)

        log_directory = Path(LOG_DIR)
        log_directory.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_directory / LOG_FILE,
            maxBytes=MAX_LOG_SIZE,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(context_filter)
        handlers = [file_handler, console_handler]

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    for handler in handlers:
        root_logger.addHandler(handler)

    # When the app is loaded by Uvicorn after its logging configuration, route
    # its records through the same stdout formatter.  Access logging is still
    # best disabled because RequestIDMiddleware emits a richer request record.
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    if asyncssh_level:
        asyncssh_log_level = _get_log_level(asyncssh_level)
        logging.getLogger("asyncssh").setLevel(asyncssh_log_level)
        logging.getLogger("asyncssh.sftp").setLevel(asyncssh_log_level)

    logging.getLogger(__name__).info(
        "Logging initialized",
        extra={"environment": environment, "structured": production},
    )


__all__ = ["JSONFormatter", "RequestContextFilter", "_get_log_level", "setup_logging"]
