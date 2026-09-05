"""Coverage for the administrator-controlled console log level."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.logging_config import (
    CONSOLE_HANDLER_NAME,
    FILE_HANDLER_NAME,
    console_log_level,
    set_console_log_level,
    setup_logging,
)
from modules.utils import normalize_log_level
from services.log_output import (
    apply_console_log_level,
    effective_console_log_level,
    refresh_console_log_level,
)


@pytest.fixture
def configured_logging():
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    setup_logging(level=logging.INFO)
    try:
        yield root
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            if handler not in saved_handlers:
                handler.close()
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)


def _handler(root: logging.Logger, name: str) -> logging.Handler:
    return next(handler for handler in root.handlers if handler.get_name() == name)


def test_normalize_log_level_accepts_known_levels_only():
    assert normalize_log_level(" error ") == "ERROR"
    assert normalize_log_level("debug") == "DEBUG"
    assert normalize_log_level("  ") is None
    assert normalize_log_level(None) is None
    for invalid in ("TRACE", "verbose", "1"):
        with pytest.raises(ValueError):
            normalize_log_level(invalid)


def test_console_level_change_does_not_quiet_the_log_file(configured_logging):
    root = configured_logging
    assert console_log_level() == logging.INFO

    assert set_console_log_level(logging.ERROR) is True
    assert _handler(root, CONSOLE_HANDLER_NAME).level == logging.ERROR
    assert _handler(root, FILE_HANDLER_NAME).level == logging.INFO
    # The root logger still passes INFO records so the file keeps them.
    assert root.level == logging.INFO


def test_console_level_also_governs_uvicorn_access_logs(configured_logging):
    access = logging.getLogger("uvicorn.access")
    saved = access.level
    try:
        set_console_log_level(logging.ERROR)
        # Uvicorn keeps propagate off, so only its own level can quiet it.
        assert access.level == logging.ERROR
        assert access.isEnabledFor(logging.INFO) is False
    finally:
        access.setLevel(saved)


def test_more_verbose_console_lowers_the_root_logger(configured_logging):
    root = configured_logging
    set_console_log_level(logging.DEBUG)
    assert root.level == logging.DEBUG
    assert _handler(root, FILE_HANDLER_NAME).level == logging.INFO


def test_set_console_log_level_without_a_console_handler_is_a_no_op():
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    for handler in saved_handlers:
        root.removeHandler(handler)
    try:
        assert set_console_log_level(logging.ERROR) is False
        assert console_log_level() is None
        assert root.level == saved_level
    finally:
        for handler in saved_handlers:
            root.addHandler(handler)


def test_blank_setting_follows_the_environment(monkeypatch):
    monkeypatch.setattr("services.log_output.settings.LOG_LEVEL", "WARNING")
    assert effective_console_log_level(None) == "WARNING"
    assert effective_console_log_level("") == "WARNING"
    assert effective_console_log_level("debug") == "DEBUG"


def test_invalid_stored_level_falls_back_to_the_environment(monkeypatch):
    monkeypatch.setattr("services.log_output.settings.LOG_LEVEL", "INFO")
    assert effective_console_log_level("TRACE") == "INFO"


def test_apply_console_log_level_updates_the_running_handler(configured_logging):
    root = configured_logging
    assert apply_console_log_level("ERROR") == "ERROR"
    assert _handler(root, CONSOLE_HANDLER_NAME).level == logging.ERROR

    assert apply_console_log_level("WARNING") == "WARNING"
    assert _handler(root, CONSOLE_HANDLER_NAME).level == logging.WARNING


@pytest.mark.asyncio
async def test_refresh_reads_and_applies_the_saved_level(monkeypatch, configured_logging):
    root = configured_logging
    monkeypatch.setattr(
        "services.log_output.SystemSettings.get_settings",
        AsyncMock(return_value=SimpleNamespace(log_level="CRITICAL")),
    )
    session = SimpleNamespace(execute=AsyncMock())

    assert await refresh_console_log_level(session) == "CRITICAL"
    assert _handler(root, CONSOLE_HANDLER_NAME).level == logging.CRITICAL


@pytest.mark.asyncio
async def test_refresh_defaults_to_error_when_no_settings_row_exists(monkeypatch):
    monkeypatch.setattr(
        "services.log_output.SystemSettings.get_settings",
        AsyncMock(return_value=None),
    )
    session = SimpleNamespace(execute=AsyncMock())
    assert await refresh_console_log_level(session) == "ERROR"


@pytest.mark.asyncio
async def test_refresh_falls_back_to_the_environment_when_the_database_fails(monkeypatch):
    monkeypatch.setattr("services.log_output.settings.LOG_LEVEL", "WARNING")
    monkeypatch.setattr(
        "services.log_output.SystemSettings.get_settings",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    session = SimpleNamespace(execute=AsyncMock())
    assert await refresh_console_log_level(session) == "WARNING"
