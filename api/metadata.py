"""Public application and build metadata shared by API components."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

APP_TITLE = "UPKK CS2 Server Manager"
APP_DESCRIPTION = (
    "Manage multiple CS2 servers via FastAPI + Redis + PostgreSQL with WebSocket support"
)
APP_VERSION = "1.0.0"

_UNKNOWN = "unknown"
_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)


def _environment_value(name: str) -> str:
    """Read an optional build value without allowing whitespace-only metadata."""
    value = os.getenv(name, "").strip()
    return value or _UNKNOWN


def _short_commit(value: str) -> str:
    """Return a safe seven-character commit identifier for health/UI output."""
    if value == _UNKNOWN or not _SHA_PATTERN.fullmatch(value):
        return _UNKNOWN
    return value[:7].lower()


def _build_time(value: str) -> str:
    """Normalize a timezone-aware ISO-8601 timestamp or return ``unknown``."""
    if value == _UNKNOWN:
        return _UNKNOWN
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _UNKNOWN
    if parsed.tzinfo is None:
        return _UNKNOWN
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# The image build injects these values through Docker ``ARG``/``ENV``.  The
# unknown fallback keeps local source checkouts usable without a VCS checkout
# or a release pipeline while preventing arbitrary environment text from
# being exposed as a commit identifier.
BUILD_COMMIT = _short_commit(_environment_value("APP_GIT_SHA"))
BUILD_TIME = _build_time(_environment_value("APP_BUILD_TIME"))
