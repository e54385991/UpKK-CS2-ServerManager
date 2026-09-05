"""Coverage for the administrator-controlled source IP policy."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from modules.models import AuditLog
from modules.utils import normalize_client_ip_header
from services.audit_log_service import client_ip_address, record_audit_event
from services.client_ip import (
    DEFAULT_CLIENT_IP_HEADER,
    cached_client_ip_header,
    current_client_ip_header,
    refresh_client_ip_header,
    request_client_ip,
    reset_client_ip_header_cache,
    resolve_client_ip,
    set_client_ip_header,
)
from services.rate_limit import enforce_rate_limit

PEER = "198.51.100.7"


@pytest.fixture(autouse=True)
def _restore_policy_cache():
    reset_client_ip_header_cache()
    yield
    reset_client_ip_header_cache()


def _request(headers: dict[str, str] | None = None, peer: str | None = PEER) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [
            (name.lower().encode(), value.encode()) for name, value in (headers or {}).items()
        ],
        "client": (peer, 51234) if peer else None,
    }
    return Request(scope)


def test_default_policy_reads_the_forwarded_header():
    request = _request({"X-Forwarded-For": "203.0.113.9, 70.41.3.18, 150.172.238.178"})
    assert resolve_client_ip(request, DEFAULT_CLIENT_IP_HEADER) == "203.0.113.9"


def test_configured_header_replaces_the_default():
    request = _request(
        {"X-Forwarded-For": "203.0.113.9", "CF-Connecting-IP": "192.0.2.44"},
    )
    assert resolve_client_ip(request, "CF-Connecting-IP") == "192.0.2.44"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("203.0.113.9:1234", "203.0.113.9"),
        ("[2001:db8::1]:41237", "2001:db8::1"),
        ("2001:db8::1", "2001:db8::1"),
        ('for="[2001:db8::1]:41237"', "2001:db8::1"),
        ("for=192.0.2.44;proto=https", "192.0.2.44"),
        ("unknown, 203.0.113.9", "203.0.113.9"),
    ],
)
def test_forwarded_values_are_parsed_into_bare_addresses(raw: str, expected: str):
    assert resolve_client_ip(_request({"X-Forwarded-For": raw}), "X-Forwarded-For") == expected


def test_unusable_header_falls_back_to_the_socket_peer():
    request = _request({"X-Forwarded-For": "not-an-address"})
    assert resolve_client_ip(request, "X-Forwarded-For") == PEER


def test_blank_policy_uses_the_socket_peer_only():
    request = _request({"X-Forwarded-For": "203.0.113.9"})
    assert resolve_client_ip(request, None) == PEER


def test_missing_peer_and_header_resolve_to_unknown():
    assert resolve_client_ip(_request(peer=None), "X-Forwarded-For") == "unknown"
    assert resolve_client_ip(None, "X-Forwarded-For") is None


def test_normalize_rejects_values_that_are_not_header_names():
    assert normalize_client_ip_header("  X-Real-IP  ") == "X-Real-IP"
    assert normalize_client_ip_header("   ") is None
    assert normalize_client_ip_header(None) is None
    for invalid in ("X Forwarded For", "X-Forwarded-For: 1", "-leading", "x" * 65):
        with pytest.raises(ValueError):
            normalize_client_ip_header(invalid)


def test_saved_policy_is_published_to_request_handling():
    assert set_client_ip_header("X-Real-IP") == "X-Real-IP"
    assert cached_client_ip_header() == "X-Real-IP"
    assert client_ip_address(_request({"X-Real-IP": "192.0.2.44"})) == "192.0.2.44"

    assert set_client_ip_header("") is None
    assert client_ip_address(_request({"X-Real-IP": "192.0.2.44"})) == PEER


def test_invalid_stored_policy_is_ignored_instead_of_breaking_requests():
    assert set_client_ip_header("X Forwarded For") is None
    assert client_ip_address(_request({"X-Forwarded-For": "203.0.113.9"})) == PEER


@pytest.mark.asyncio
async def test_policy_is_loaded_from_the_database_and_then_cached(monkeypatch):
    loads = 0

    async def _get_settings(_session):
        nonlocal loads
        loads += 1
        return SimpleNamespace(client_ip_header="CF-Connecting-IP")

    monkeypatch.setattr(
        "services.client_ip.SystemSettings.get_settings",
        AsyncMock(side_effect=_get_settings),
    )
    session = SimpleNamespace(execute=AsyncMock())

    assert await current_client_ip_header(session) == "CF-Connecting-IP"
    assert await current_client_ip_header(session) == "CF-Connecting-IP"
    assert loads == 1


@pytest.mark.asyncio
async def test_database_failure_keeps_the_last_known_policy(monkeypatch):
    set_client_ip_header("X-Real-IP")
    monkeypatch.setattr(
        "services.client_ip.SystemSettings.get_settings",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    session = SimpleNamespace(execute=AsyncMock())
    assert await refresh_client_ip_header(session) == "X-Real-IP"


@pytest.mark.asyncio
async def test_missing_settings_row_falls_back_to_the_default_header(monkeypatch):
    monkeypatch.setattr(
        "services.client_ip.SystemSettings.get_settings",
        AsyncMock(return_value=None),
    )
    session = SimpleNamespace(execute=AsyncMock())
    assert await refresh_client_ip_header(session) == DEFAULT_CLIENT_IP_HEADER


@pytest.mark.asyncio
async def test_audit_events_record_the_configured_source_address(monkeypatch):
    class _Session:
        def __init__(self):
            self.added = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        def add(self, item):
            self.added.append(item)

        async def commit(self):
            return None

    session = _Session()
    monkeypatch.setattr("services.audit_log_service.async_session_maker", lambda: session)
    set_client_ip_header("X-Forwarded-For")

    await record_audit_event(
        category="auth",
        action="auth.login",
        status="success",
        request=_request({"X-Forwarded-For": "203.0.113.9, 10.0.0.3"}),
    )

    item = session.added[0]
    assert isinstance(item, AuditLog)
    assert item.ip_address == "203.0.113.9"


@pytest.mark.asyncio
async def test_rate_limit_buckets_follow_the_configured_source_address(monkeypatch):
    keys: list[str] = []

    async def _hit(key, _limit, _window):
        keys.append(key)
        return True, 0

    monkeypatch.setattr("services.rate_limit.redis_manager.hit_rate_limit", _hit)
    set_client_ip_header("X-Forwarded-For")

    await enforce_rate_limit(
        _request({"X-Forwarded-For": "203.0.113.9"}),
        "captcha",
        limit=5,
        window=60,
    )

    expected = hashlib.sha256(b"203.0.113.9:").hexdigest()
    assert keys == [f"rate_limit:captcha:{expected}"]


@pytest.mark.asyncio
async def test_rate_limit_still_raises_when_the_bucket_is_exhausted(monkeypatch):
    monkeypatch.setattr(
        "services.rate_limit.redis_manager.hit_rate_limit",
        AsyncMock(return_value=(False, 12)),
    )
    set_client_ip_header(None)

    with pytest.raises(HTTPException) as exc_info:
        await enforce_rate_limit(_request(), "captcha", limit=5, window=60)

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "12"}


@pytest.mark.asyncio
async def test_request_client_ip_returns_none_without_a_request():
    assert await request_client_ip(None) is None
