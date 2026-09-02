"""SteamCMD unexpected-exit recovery: retry budget, backoff, and user override."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

STEAMCMD_DEFAULT_MAX_RETRIES = 20
STEAMCMD_MAX_RETRIES_LIMIT = 100
STEAMCMD_RETRY_DELAY_SECONDS = 5
STEAMCMD_RETRY_DELAY_MAX_SECONDS = 60

# Permanent failures that should not burn the retry budget.
STEAMCMD_NON_RETRYABLE_ERRORS = (
    "no space left",
    "disk full",
    "not enough disk",
    "not enough free disk",
    "access denied",
    "login failure",
    "invalid password",
    "no subscription",
)

# Common transient SteamCMD / network / crash phrases (used for progress copy).
STEAMCMD_RETRYABLE_ERRORS = (
    "timeout",
    "timed out",
    "connection",
    "network",
    "failed to download",
    "download failed",
    "corrupt",
    "error downloading",
    "unable to download",
    "http error",
    "failed to install",
    "no connection",
    "connection reset",
    "broken pipe",
    "segmentation fault",
    "segfault",
    "killed",
    "aborted",
    "unexpected",
)


def clamp_steamcmd_max_retries(value: object) -> int:
    """Coerce a stored or submitted retry budget to ``0..100``, default 20."""
    if value is None:
        return STEAMCMD_DEFAULT_MAX_RETRIES
    if not isinstance(value, (int, float, str)):
        return STEAMCMD_DEFAULT_MAX_RETRIES
    try:
        parsed = int(value)
    except TypeError, ValueError:
        return STEAMCMD_DEFAULT_MAX_RETRIES
    return max(0, min(STEAMCMD_MAX_RETRIES_LIMIT, parsed))


def steamcmd_retry_delay_seconds(attempt: int, base_seconds: float | int | None = None) -> float:
    """Exponential backoff for retry *attempt* (1-based), capped at 60s.

    A zero base (used by unit tests) stays zero so suites do not sleep.
    """
    base = STEAMCMD_RETRY_DELAY_SECONDS if base_seconds is None else max(0.0, float(base_seconds))
    if base == 0:
        return 0.0
    exponent = max(0, int(attempt) - 1)
    return min(base * (2**exponent), float(STEAMCMD_RETRY_DELAY_MAX_SECONDS))


def is_steamcmd_failure_retryable(stdout: str = "", stderr: str = "") -> bool:
    """Network drops, crashes, and other unexpected exits are retryable."""
    output = f"{stderr or ''} {stdout or ''}".lower()
    return not any(token in output for token in STEAMCMD_NON_RETRYABLE_ERRORS)


async def resolve_steamcmd_max_retries(user_id: int | None) -> int:
    """Load the owner's personal-center retry budget, or the default of 20."""
    if user_id is None:
        return STEAMCMD_DEFAULT_MAX_RETRIES
    try:
        from modules.database import async_session_maker
        from modules.models import User

        async with async_session_maker() as db:
            user = await db.get(User, user_id)
            if user is None:
                return STEAMCMD_DEFAULT_MAX_RETRIES
            return clamp_steamcmd_max_retries(getattr(user, "steamcmd_max_retries", None))
    except Exception as exc:
        logger.warning(
            "Could not load SteamCMD retry setting for user %s: %s",
            user_id,
            exc,
        )
        return STEAMCMD_DEFAULT_MAX_RETRIES
