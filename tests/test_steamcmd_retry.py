"""SteamCMD unexpected-exit recovery budget and retry classification."""

from __future__ import annotations

import pytest

from services.steamcmd_retry import (
    STEAMCMD_DEFAULT_MAX_RETRIES,
    STEAMCMD_MAX_RETRIES_LIMIT,
    STEAMCMD_RETRY_DELAY_MAX_SECONDS,
    clamp_steamcmd_max_retries,
    is_steamcmd_failure_retryable,
    steamcmd_retry_delay_seconds,
)


def test_default_retry_budget_is_twenty():
    assert STEAMCMD_DEFAULT_MAX_RETRIES == 20
    assert clamp_steamcmd_max_retries(None) == 20
    assert clamp_steamcmd_max_retries("not-a-number") == 20


def test_retry_budget_is_clamped():
    assert clamp_steamcmd_max_retries(-3) == 0
    assert clamp_steamcmd_max_retries(0) == 0
    assert clamp_steamcmd_max_retries(7) == 7
    assert clamp_steamcmd_max_retries(STEAMCMD_MAX_RETRIES_LIMIT + 50) == STEAMCMD_MAX_RETRIES_LIMIT


def test_backoff_is_capped_so_twenty_retries_stay_usable():
    assert steamcmd_retry_delay_seconds(1, base_seconds=5) == 5
    assert steamcmd_retry_delay_seconds(2, base_seconds=5) == 10
    assert steamcmd_retry_delay_seconds(20, base_seconds=5) == STEAMCMD_RETRY_DELAY_MAX_SECONDS
    assert steamcmd_retry_delay_seconds(3, base_seconds=0) == 0


def test_network_and_crash_exits_are_retryable():
    assert is_steamcmd_failure_retryable("", "Connection reset by peer")
    assert is_steamcmd_failure_retryable("", "Segmentation fault")
    assert is_steamcmd_failure_retryable("", "Killed")
    assert is_steamcmd_failure_retryable("", "")
    assert is_steamcmd_failure_retryable("SteamCMD", "timeout waiting for steam")


def test_permanent_disk_and_login_failures_are_not_retryable():
    assert is_steamcmd_failure_retryable("", "No space left on device") is False
    assert is_steamcmd_failure_retryable("", "Login Failure") is False


@pytest.mark.asyncio
async def test_resolve_uses_user_setting(monkeypatch):
    class FakeUser:
        steamcmd_max_retries = 8

    class FakeSession:
        async def get(self, _model, user_id):
            assert user_id == 4
            return FakeUser()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(
        "modules.database.async_session_maker",
        lambda: FakeSession(),
    )
    from services.steamcmd_retry import resolve_steamcmd_max_retries

    assert await resolve_steamcmd_max_retries(4) == 8
    assert await resolve_steamcmd_max_retries(None) == 20
