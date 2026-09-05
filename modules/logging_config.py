"""
Logging configuration for CS2 Server Manager
Configures rotating file handler with automatic log rotation
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional, Protocol, cast

# Log directory and file settings
LOG_DIR = "logs"
LOG_FILE = "cs2_manager.log"
MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 10  # Keep 10 backup files
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Names the console and file handlers so the administrator-controlled console
# level can be changed later without disturbing what is written to disk.
CONSOLE_HANDLER_NAME = "cs2-console"
FILE_HANDLER_NAME = "cs2-file"

# Uvicorn owns these loggers and keeps ``propagate`` off, so they never reach
# the handlers above. Their per-request access lines are the bulk of console
# noise, so the administrator's console level has to govern them by name.
CONSOLE_ONLY_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class _ReconfigurableStream(Protocol):
    def reconfigure(self, *, line_buffering: bool) -> None: ...


def _get_log_level(level_str: str) -> int:
    """
    Convert string log level to logging constant

    Args:
        level_str: Log level as string (DEBUG, INFO, WARNING, ERROR, CRITICAL)

    Returns:
        Logging level constant (defaults to INFO if invalid)
    """
    if not level_str or not isinstance(level_str, str):
        return logging.INFO

    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    return level_map.get(level_str.upper(), logging.INFO)


def console_log_level() -> Optional[int]:
    """The level stdout is currently filtered at, or None before setup."""
    for handler in logging.getLogger().handlers:
        if handler.get_name() == CONSOLE_HANDLER_NAME:
            return handler.level
    return None


def set_console_log_level(level: int) -> bool:
    """
    Change how much reaches stdout without changing what the log file keeps.

    The root logger is lowered to whichever handler wants the most detail, so
    raising the console level cannot silence the file handler and vice versa.

    Args:
        level: Logging level constant for the console handler

    Returns:
        True when a console handler was found and updated
    """
    root_logger = logging.getLogger()
    console_handlers = [
        handler for handler in root_logger.handlers if handler.get_name() == CONSOLE_HANDLER_NAME
    ]
    if not console_handlers:
        return False

    for handler in console_handlers:
        handler.setLevel(level)
    # A handler left at NOTSET defers to the logger, so it cannot raise the floor.
    handler_levels = [handler.level for handler in root_logger.handlers if handler.level]
    root_logger.setLevel(min(handler_levels) if handler_levels else level)
    for name in CONSOLE_ONLY_LOGGERS:
        logging.getLogger(name).setLevel(level)
    return True


def setup_logging(level: int = logging.INFO, asyncssh_level: Optional[str] = None) -> None:
    """
    Configure logging with rotating file handler.

    Args:
        level: Logging level (default: INFO)
        asyncssh_level: AsyncSSH logging level as string (e.g., "WARNING", "ERROR")
                       If None, uses the same level as general logging
    """
    # Create logs directory if it doesn't exist
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)

    log_file_path = os.path.join(LOG_DIR, LOG_FILE)

    # Create formatter
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # Create rotating file handler
    file_handler = RotatingFileHandler(
        log_file_path, maxBytes=MAX_LOG_SIZE, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    file_handler.set_name(FILE_HANDLER_NAME)

    # Line-buffer Docker/1Panel pipes so startup lines are not lost on SIGKILL.
    if hasattr(sys.stdout, "reconfigure"):
        cast(_ReconfigurableStream, sys.stdout).reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        cast(_ReconfigurableStream, sys.stderr).reconfigure(line_buffering=True)

    # Create console handler for stdout
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    console_handler.set_name(CONSOLE_HANDLER_NAME)

    # Get root logger and configure it
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Add handlers
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Configure asyncssh logging level separately
    if asyncssh_level:
        asyncssh_log_level = _get_log_level(asyncssh_level)
        logging.getLogger("asyncssh").setLevel(asyncssh_log_level)
        logging.getLogger("asyncssh.sftp").setLevel(asyncssh_log_level)
        logging.info(f"AsyncSSH logging level set to: {asyncssh_level}")

    # Log startup message
    logging.info(
        f"Logging initialized - file: {log_file_path}, max size: {MAX_LOG_SIZE // (1024 * 1024)}MB, backups: {BACKUP_COUNT}"
    )
