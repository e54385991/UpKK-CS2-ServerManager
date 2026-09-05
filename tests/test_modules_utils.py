"""Coverage for the foundational helpers in :mod:`modules.utils`."""

from __future__ import annotations

import string
from datetime import timezone

from modules.utils import (
    generate_api_key,
    get_current_time,
    verify_api_key_format,
)


def test_generated_api_keys_are_unique_and_alphanumeric():
    first = generate_api_key()
    second = generate_api_key()
    assert len(first) == 64
    assert first != second
    assert set(first) <= set(string.ascii_letters + string.digits)
    assert len(generate_api_key(32)) == 32


def test_api_key_format_accepts_generated_keys_only():
    assert verify_api_key_format(generate_api_key()) is True
    assert verify_api_key_format("") is False
    assert verify_api_key_format("short") is False
    assert verify_api_key_format("x" * 65) is False
    assert verify_api_key_format("-" * 64) is False


def test_current_time_follows_the_tz_environment_variable(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    moment = get_current_time()
    assert moment.tzinfo is not None
    assert moment.utcoffset() == timezone.utc.utcoffset(None)


def test_current_time_falls_back_when_tz_is_unusable(monkeypatch):
    monkeypatch.setenv("TZ", "Not/AZone")
    moment = get_current_time()
    # An invalid TZ must not raise; the system timezone is used instead.
    assert moment.tzinfo is not None
