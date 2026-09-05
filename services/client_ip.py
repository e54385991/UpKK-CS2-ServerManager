"""Resolve the client address the panel attributes to an HTTP request.

Deployments differ in how the real client address reaches the panel: a direct
bind exposes it as the socket peer, while nginx, Caddy, 1Panel, or Cloudflare
forward it in a request header. Administrators choose that header in system
settings, and everything that records or limits by address (audit logs, public
rate limits) reads the resolved policy from here instead of hard-coding one
deployment shape.
"""

from __future__ import annotations

import ipaddress
import logging
import time

from fastapi import Request

from modules.database import async_session_maker
from modules.models.system import SystemSettings
from modules.utils import DEFAULT_CLIENT_IP_HEADER, normalize_client_ip_header

logger = logging.getLogger(__name__)

UNKNOWN_CLIENT_ADDRESS = "unknown"

# The policy changes only when an administrator saves system settings, so a
# short time-to-live keeps request handling off the database while still
# picking up a change made by another panel process within one interval.
_POLICY_TTL_SECONDS = 30.0

_cached_header: str | None = DEFAULT_CLIENT_IP_HEADER
_cached_at: float | None = None


def _safe_header(value: str | None) -> str | None:
    """Never let a stored or stale value break request handling."""
    try:
        return normalize_client_ip_header(value)
    except ValueError:
        logger.warning("Ignoring invalid client IP header setting: %r", value)
        return None


def cached_client_ip_header() -> str | None:
    """Return the last known policy without touching the database."""
    return _cached_header


def set_client_ip_header(value: str | None) -> str | None:
    """Publish a freshly saved policy to every request path in this process."""
    global _cached_header, _cached_at
    _cached_header = _safe_header(value)
    _cached_at = time.monotonic()
    return _cached_header


def reset_client_ip_header_cache() -> None:
    """Drop the cached policy so the next read reloads it (tests, startup)."""
    global _cached_header, _cached_at
    _cached_header = DEFAULT_CLIENT_IP_HEADER
    _cached_at = None


async def refresh_client_ip_header(session=None) -> str | None:
    """Reload the policy from the database, keeping the last value on failure."""
    try:
        if session is not None and callable(getattr(session, "execute", None)):
            settings = await SystemSettings.get_settings(session)
        else:
            async with async_session_maker() as db:
                settings = await SystemSettings.get_settings(db)
    except Exception:
        # A migration race or a transient database failure must not change how
        # addresses are attributed; keep serving the last known policy and stop
        # retrying on every request until the next TTL window.
        logger.debug("Could not refresh the client IP header policy", exc_info=True)
        global _cached_at
        _cached_at = time.monotonic()
        return _cached_header
    if settings is None:
        return set_client_ip_header(DEFAULT_CLIENT_IP_HEADER)
    return set_client_ip_header(settings.client_ip_header)


async def current_client_ip_header(session=None) -> str | None:
    """Return the policy, refreshing it at most once per TTL window."""
    if _cached_at is not None and (time.monotonic() - _cached_at) < _POLICY_TTL_SECONDS:
        return _cached_header
    return await refresh_client_ip_header(session)


def peer_address(request: Request | None) -> str | None:
    """The address of the socket the request arrived on."""
    if request is None or request.client is None:
        return None
    return request.client.host or None


def _parse_forwarded_address(token: str) -> str | None:
    """Read one forwarded list element, tolerating ports and RFC 7239 syntax."""
    text = token.strip()
    if not text:
        return None
    # `Forwarded: for=192.0.2.1;proto=https` and quoted IPv6 forms.
    text = text.split(";", 1)[0].strip()
    if text.lower().startswith("for="):
        text = text[4:].strip()
    text = text.strip('"')
    if text.startswith("["):
        host, closing, _rest = text.partition("]")
        if not closing:
            return None
        text = host[1:]
    elif text.count(":") == 1:
        text = text.split(":", 1)[0]
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return None
    return text


def forwarded_address(request: Request | None, header: str | None) -> str | None:
    """The left-most valid address the configured header carries, if any."""
    if request is None or not header:
        return None
    # Internal callers and test doubles may pass a request-like object without
    # headers; attribution must never raise into the caller.
    headers = getattr(request, "headers", None)
    raw = headers.get(header) if headers is not None else None
    if not raw:
        return None
    for token in raw.split(","):
        address = _parse_forwarded_address(token)
        if address is not None:
            return address
    return None


def resolve_client_ip(request: Request | None, header: str | None) -> str | None:
    """Attribute a request to an address under the given policy."""
    if request is None:
        return None
    return forwarded_address(request, header) or peer_address(request) or UNKNOWN_CLIENT_ADDRESS


async def request_client_ip(request: Request | None, session=None) -> str | None:
    """Attribute a request to an address under the persisted policy."""
    if request is None:
        return None
    return resolve_client_ip(request, await current_client_ip_header(session))
