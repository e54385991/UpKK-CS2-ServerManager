"""Relying-party origin and rpId derivation for passkeys."""

from __future__ import annotations

import pytest

from services.passkeys.rp import hostname_is_ip, normalize_origin, resolve_relying_party
from services.passkeys.types import PasskeyError


def test_normalize_origin_keeps_explicit_port_and_lowercases_host():
    assert (
        normalize_origin("https://Panel.Example.com:8443/ignored")
        == "https://panel.example.com:8443"
    )


def test_hostname_is_ip_detects_v4_and_v6():
    assert hostname_is_ip("192.168.1.8") is True
    assert hostname_is_ip("::1") is True
    assert hostname_is_ip("[::1]") is True
    assert hostname_is_ip("localhost") is False
    assert hostname_is_ip("panel.example.com") is False


def test_resolve_relying_party_allows_localhost_http(monkeypatch):
    monkeypatch.setattr("services.passkeys.rp.get_settings", lambda: _Settings())
    rp = resolve_relying_party(origin_header="http://localhost:31800")
    assert rp.origin == "http://localhost:31800"
    assert rp.rp_id == "localhost"


def test_resolve_relying_party_rejects_ip_and_insecure_lan(monkeypatch):
    monkeypatch.setattr("services.passkeys.rp.get_settings", lambda: _Settings())
    with pytest.raises(PasskeyError) as ip_error:
        resolve_relying_party(origin_header="http://192.168.1.8:31800")
    assert ip_error.value.code == "passkey_ip_origin"
    with pytest.raises(PasskeyError) as insecure:
        resolve_relying_party(origin_header="http://panel.example.com")
    assert insecure.value.code == "passkey_insecure_origin"


def test_resolve_relying_party_uses_configured_origin_and_rp_id(monkeypatch):
    monkeypatch.setattr(
        "services.passkeys.rp.get_settings",
        lambda: _Settings(origin="https://panel.example.com", rp_id="example.com"),
    )
    rp = resolve_relying_party(origin_header="https://panel.example.com")
    assert rp.origin == "https://panel.example.com"
    assert rp.rp_id == "example.com"


def test_resolve_relying_party_rejects_origin_mismatch(monkeypatch):
    monkeypatch.setattr(
        "services.passkeys.rp.get_settings",
        lambda: _Settings(origin="https://panel.example.com"),
    )
    with pytest.raises(PasskeyError) as exc:
        resolve_relying_party(origin_header="https://evil.example")
    assert exc.value.code == "passkey_origin_mismatch"


class _Settings:
    def __init__(self, origin: str = "", rp_id: str = "") -> None:
        self.WEBAUTHN_ORIGIN = origin
        self.WEBAUTHN_RP_ID = rp_id
