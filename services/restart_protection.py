"""Per-server crash-loop protection window.

The live limiter is this duration, not the legacy global
``auto_restart_time_window_minutes`` setting.
"""

from __future__ import annotations

from datetime import timedelta

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
