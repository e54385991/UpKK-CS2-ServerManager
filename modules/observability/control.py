"""Process-local monitoring switch, generation, and instance identity.

This module is I/O-free. Enabling and disabling only flips memory state;
callers own starting or stopping background work.
"""

from __future__ import annotations

import os
import threading
import uuid
from time import time

_lock = threading.Lock()
_enabled = False
_generation = 0
_instance_id = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
_stopped_at: float | None = None
_started_at: float | None = None
_sync_error: str | None = None


def instance_id() -> str:
    return _instance_id


def is_enabled() -> bool:
    return _enabled


def generation() -> int:
    return _generation


def stopped_at() -> float | None:
    return _stopped_at


def started_at() -> float | None:
    return _started_at


def sync_error() -> str | None:
    return _sync_error


def set_sync_error(message: str | None) -> None:
    global _sync_error
    _sync_error = message


def set_enabled(enabled: bool) -> int:
    """Apply the in-memory switch. Returns the generation after the change."""
    global _enabled, _generation, _stopped_at, _started_at
    with _lock:
        changed = enabled != _enabled
        if changed or _generation == 0:
            _generation += 1
            _enabled = enabled
            now = time()
            if enabled:
                _stopped_at = None
                _started_at = now
            else:
                _stopped_at = now
        return _generation


def reset_for_tests() -> None:
    """Restore process-local state. Used by unit tests only."""
    global _enabled, _generation, _instance_id, _stopped_at, _started_at, _sync_error
    with _lock:
        _enabled = False
        _generation = 0
        _instance_id = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        _stopped_at = None
        _started_at = None
        _sync_error = None
