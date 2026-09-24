"""Per-server crash-loop protection window.

The live limiter is this duration, not the legacy global
``auto_restart_time_window_minutes`` setting.
"""

from __future__ import annotations

import logging
import shlex
from datetime import timedelta

logger = logging.getLogger(__name__)

DEFAULT_RESTART_PROTECTION_HOURS = 2
MAX_RESTART_PROTECTION_HOURS = 720


def restart_protection_hours(value: object | None = None) -> int:
    """Clamp a stored or requested window to 1–720 hours, defaulting to 2."""
    if value is None or isinstance(value, bool):
        return DEFAULT_RESTART_PROTECTION_HOURS
    try:
        hours = int(str(value))
    except TypeError, ValueError:
        return DEFAULT_RESTART_PROTECTION_HOURS
    if hours < 1:
        return 1
    if hours > MAX_RESTART_PROTECTION_HOURS:
        return MAX_RESTART_PROTECTION_HOURS
    return hours


def restart_protection_window(server: object | None = None) -> timedelta:
    """Return the protection window for a server, or the 2-hour default."""
    raw = getattr(server, "restart_protection_hours", None) if server is not None else None
    return timedelta(hours=restart_protection_hours(raw))


async def clear_operator_crash_protection(ssh_manager, server) -> str | None:
    """Drop the in-memory counter and the host crash log for an operator action.

    The auto-restart wrapper exits immediately once ``crash_history.log``
    already holds five crashes, before CS2 creates ``console.log``. A manual
    start, stop, restart, or update must clear both stores first.
    """
    from services.server_monitor import server_monitor

    server_monitor.reset_restart_history(server.id)
    crash_log = shlex.quote(f"{server.game_directory}/crash_history.log")
    try:
        connected, detail = await ssh_manager.connect(server)
        if not connected:
            return f"Could not clear crash history: {detail}"
        ok, _stdout, stderr = await ssh_manager.execute_command(f"rm -f {crash_log}")
        await ssh_manager.disconnect()
        if not ok:
            return f"Could not clear crash history: {stderr or 'remote remove failed'}"
    except Exception as exc:
        logger.warning("Failed to clear crash history for server %s: %s", server.id, exc)
        return f"Could not clear crash history: {exc}"
    return None
