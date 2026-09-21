"""Derive WebAuthn origin and rpId from the browser, not the FastAPI bind host."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from modules.config import get_settings
from services.passkeys.types import PasskeyError, RelyingParty

LOCALHOST_LABELS = frozenset({"localhost", "localhost.localdomain"})


def hostname_is_ip(hostname: str) -> bool:
    """Return True when the host is a literal IPv4 or IPv6 address."""
    candidate = hostname.strip().strip("[]")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


def origin_parts(raw: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(raw.strip())
    hostname = (parsed.hostname or "").casefold()
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise PasskeyError("passkey_missing_origin")
    return parsed.scheme, hostname, parsed.port


def normalize_origin(raw: str) -> str:
    scheme, hostname, port = origin_parts(raw)
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port:
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


def resolve_relying_party(*, origin_header: str | None) -> RelyingParty:
    """Resolve RP identity from WEBAUTHN_* overrides or the Origin header."""
    settings = get_settings()
    configured = (settings.WEBAUTHN_ORIGIN or "").strip()
    header = (origin_header or "").strip()
    origin = _select_origin(configured, header)
    _scheme, hostname, _port = origin_parts(origin)
    if hostname_is_ip(hostname):
        raise PasskeyError("passkey_ip_origin")
    if origin.startswith("http://") and hostname not in LOCALHOST_LABELS:
        raise PasskeyError("passkey_insecure_origin")
    return RelyingParty(origin=origin, rp_id=_select_rp_id(hostname))


def _select_origin(configured: str, header: str) -> str:
    if configured:
        origin = normalize_origin(configured)
        if header and normalize_origin(header) != origin:
            raise PasskeyError("passkey_origin_mismatch")
        return origin
    if header:
        return normalize_origin(header)
    raise PasskeyError("passkey_missing_origin")


def _select_rp_id(hostname: str) -> str:
    configured = (get_settings().WEBAUTHN_RP_ID or "").strip().casefold()
    if not configured:
        return hostname
    if hostname != configured and not hostname.endswith(f".{configured}"):
        raise PasskeyError("passkey_rp_mismatch")
    return configured
