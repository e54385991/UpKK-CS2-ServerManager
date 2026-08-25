"""Shared policy for explicit user lifecycle intent and background starts."""

from typing import Any

MANUAL_STOP_BLOCK_REASON = (
    "Server is being kept stopped by a user request; use a manual Start or Restart "
    "action to resume automatic starts"
)


def apply_user_lifecycle_intent(server: Any, action: str) -> bool:
    """Apply intent from an explicit user lifecycle action.

    Returns whether the persisted value changed. Internal stop/start calls must
    not use this helper because they are implementation details of a larger
    operation, not a new user intent.
    """
    if action == "stop":
        requested = True
    elif action in {"start", "restart"}:
        requested = False
    else:
        return False

    changed = bool(getattr(server, "manual_stop_requested", False)) != requested
    server.manual_stop_requested = requested
    return changed


def automatic_start_block_reason(server: Any) -> str | None:
    """Return the reason background services may not start ``server``."""
    if bool(getattr(server, "manual_stop_requested", False)):
        return MANUAL_STOP_BLOCK_REASON
    return None
