"""Shared GitHub plugin install retry policy.

Install execution lives behind several public facades. Keep the retry budget
and non-retryable markers in one place so GitHub installs, market installs,
and conflict-plan execution cannot drift apart.
"""

from __future__ import annotations

PLUGIN_INSTALL_MAX_RETRIES = 2

_NON_RETRYABLE_INSTALL_ERRORS = (
    "server not found",
    "cs2 server not found",
    "invalid custom install path",
    "release archive digest changed",
    "approved archive source prefix was not found",
    "approved archive mapping did not contain addons",
)


def _is_retryable_install_failure(message: str) -> bool:
    lowered = message.casefold()
    return not any(marker in lowered for marker in _NON_RETRYABLE_INSTALL_ERRORS)
