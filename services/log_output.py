"""Apply the administrator-chosen console log level.

Panels running under Docker, 1Panel, or systemd send stdout straight to an
operator's console, where INFO-level chatter from background monitors buries
what matters. Administrators pick how much reaches stdout in system settings;
the rotating log file keeps the environment's ``LOG_LEVEL`` either way, so
lowering console noise never costs a support trace.
"""

from __future__ import annotations

import logging

from modules.config import settings
from modules.database import async_session_maker
from modules.logging_config import _get_log_level, set_console_log_level
from modules.models.system import SystemSettings
from modules.utils import DEFAULT_CONSOLE_LOG_LEVEL, normalize_log_level

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_CONSOLE_LOG_LEVEL",
    "apply_console_log_level",
    "effective_console_log_level",
    "refresh_console_log_level",
]


def _safe_level_name(value: str | None) -> str | None:
    """Never let a stored value that is no longer valid break logging."""
    try:
        return normalize_log_level(value)
    except ValueError:
        logger.warning("Ignoring invalid console log level setting: %r", value)
        return None


def effective_console_log_level(value: str | None) -> str:
    """Resolve a stored setting, where blank means "follow the environment"."""
    return _safe_level_name(value) or _safe_level_name(settings.LOG_LEVEL) or "INFO"


def apply_console_log_level(value: str | None) -> str:
    """Publish a console level to the running process and report what applied."""
    name = effective_console_log_level(value)
    set_console_log_level(_get_log_level(name))
    return name


async def refresh_console_log_level(session=None) -> str:
    """Load the console level from the database and apply it."""
    stored: str | None = None
    try:
        if session is not None and callable(getattr(session, "execute", None)):
            row = await SystemSettings.get_settings(session)
        else:
            async with async_session_maker() as db:
                row = await SystemSettings.get_settings(db)
        stored = row.log_level if row is not None else DEFAULT_CONSOLE_LOG_LEVEL
    except Exception:
        # A migration race or a transient database failure must not leave the
        # panel without logging; fall back to the environment level.
        logger.debug("Could not read the console log level setting", exc_info=True)
    return apply_console_log_level(stored)
