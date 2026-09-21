"""Ceremony orchestration for passkey registration and authentication."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.passkeys.ceremonies import (
    _cloned_sign_count,
    complete_authentication,
    complete_registration,
    start_authentication,
    start_registration,
)
from services.passkeys.types import PasskeyError


def _user():
    return SimpleNamespace(id=4, username="ops", is_active=True)


def _rp_settings(monkeypatch):
    monkeypatch.setattr(
        "services.passkeys.rp.get_settings",
        lambda: SimpleNamespace(WEBAUTHN_ORIGIN="", WEBAUTHN_RP_ID=""),
    )


def test_cloned_sign_count_ignores_zero_counters():
    assert _cloned_sign_count(0, 0) is False
    assert _cloned_sign_count(5, 0) is False
    assert _cloned_sign_count(5, 6) is False
    assert _cloned_sign_count(5, 5) is True
    assert _cloned_sign_count(5, 3) is True


@pytest.mark.asyncio
async def test_start_registration_stores_challenge_and_returns_options(monkeypatch):
    _rp_settings(monkeypatch)
    monkeypatch.setattr(
        "services.passkeys.ceremonies.WebAuthnCredential.list_for_user",
        AsyncMock(return_value=[]),
    )
    store = AsyncMock()
    monkeypatch.setattr("services.passkeys.ceremonies.store_challenge", store)
    options = await start_registration(
        origin_header="http://localhost:31800",
        user=_user(),
        db=Mock(),
    )
    assert options["rp"]["id"] == "localhost"
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["authenticatorSelection"]["residentKey"] == "required"
    store.assert_awaited()


@pytest.mark.asyncio
async def test_start_registration_rejects_when_at_limit(monkeypatch):
    _rp_settings(monkeypatch)
    monkeypatch.setattr(
        "services.passkeys.ceremonies.WebAuthnCredential.list_for_user",
        AsyncMock(return_value=[object()] * 16),
    )
    with pytest.raises(PasskeyError) as exc:
        await start_registration(origin_header="http://localhost", user=_user(), db=Mock())
    assert exc.value.code == "passkey_limit_reached"


@pytest.mark.asyncio
async def test_complete_registration_persists_verified_credential(monkeypatch):
    _rp_settings(monkeypatch)
    monkeypatch.setattr(
        "services.passkeys.ceremonies.consume_challenge",
        AsyncMock(
            return_value={
                "type": "register",
                "origin": "http://localhost:31800",
                "rp_id": "localhost",
                "user_id": 4,
                "challenge": "Y2hhbGxlbmdl",
            }
        ),
    )
    monkeypatch.setattr(
        "services.passkeys.ceremonies.challenge_b64_from_credential",
        lambda _credential: "Y2hhbGxlbmdl",
    )
    monkeypatch.setattr(
        "services.passkeys.ceremonies.WebAuthnCredential.count_for_user",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "services.passkeys.ceremonies.WebAuthnCredential.get_by_credential_id",
        AsyncMock(return_value=None),
    )
    from webauthn.helpers.structs import CredentialDeviceType

    monkeypatch.setattr(
        "services.passkeys.ceremonies.verify_registration_response",
        lambda **_kwargs: SimpleNamespace(
            credential_id=b"cred-id",
            credential_public_key=b"public",
            sign_count=1,
            aaguid="00000000-0000-0000-0000-000000000000",
            credential_backed_up=True,
            credential_device_type=CredentialDeviceType.MULTI_DEVICE,
        ),
    )
    db = SimpleNamespace(add=Mock(), commit=AsyncMock(), refresh=AsyncMock())
    row = await complete_registration(
        origin_header="http://localhost:31800",
        user=_user(),
        db=db,
        credential={"response": {}},
        nickname="  Laptop  ",
    )
    assert row.user_id == 4
    assert row.nickname == "Laptop"
    assert row.backup_eligible is True
    db.add.assert_called()
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_complete_authentication_updates_sign_count(monkeypatch):
    _rp_settings(monkeypatch)
    monkeypatch.setattr(
        "services.passkeys.ceremonies.consume_challenge",
        AsyncMock(
            return_value={
                "type": "login",
                "origin": "http://localhost:31800",
                "rp_id": "localhost",
                "user_id": None,
                "challenge": "Y2hhbGxlbmdl",
            }
        ),
    )
    monkeypatch.setattr(
        "services.passkeys.ceremonies.challenge_b64_from_credential",
        lambda _credential: "Y2hhbGxlbmdl",
    )
    monkeypatch.setattr(
        "services.passkeys.ceremonies._credential_id_b64",
        lambda _credential: "credential",
    )
    stored = SimpleNamespace(
        user_id=4,
        public_key=b"public",
        sign_count=1,
        backup_state=False,
        last_used_at=None,
    )
    monkeypatch.setattr(
        "services.passkeys.ceremonies.WebAuthnCredential.get_by_credential_id",
        AsyncMock(return_value=stored),
    )
    monkeypatch.setattr(
        "services.passkeys.ceremonies.verify_authentication_response",
        lambda **_kwargs: SimpleNamespace(new_sign_count=2, credential_backed_up=True),
    )
    user = _user()
    db = SimpleNamespace(get=AsyncMock(return_value=user), add=Mock(), commit=AsyncMock())
    result = await complete_authentication(
        origin_header="http://localhost:31800",
        db=db,
        credential={"id": "credential", "response": {}},
    )
    assert result is user
    assert stored.sign_count == 2
    assert stored.backup_state is True
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_complete_authentication_rejects_cloned_authenticator(monkeypatch):
    _rp_settings(monkeypatch)
    monkeypatch.setattr(
        "services.passkeys.ceremonies.consume_challenge",
        AsyncMock(
            return_value={
                "type": "login",
                "origin": "http://localhost:31800",
                "rp_id": "localhost",
                "user_id": None,
                "challenge": "Y2hhbGxlbmdl",
            }
        ),
    )
    monkeypatch.setattr(
        "services.passkeys.ceremonies.challenge_b64_from_credential",
        lambda _credential: "Y2hhbGxlbmdl",
    )
    monkeypatch.setattr(
        "services.passkeys.ceremonies._credential_id_b64",
        lambda _credential: "credential",
    )
    monkeypatch.setattr(
        "services.passkeys.ceremonies.WebAuthnCredential.get_by_credential_id",
        AsyncMock(return_value=SimpleNamespace(user_id=4, public_key=b"k", sign_count=9)),
    )
    monkeypatch.setattr(
        "services.passkeys.ceremonies.verify_authentication_response",
        lambda **_kwargs: SimpleNamespace(new_sign_count=9, credential_backed_up=False),
    )
    with pytest.raises(PasskeyError) as exc:
        await complete_authentication(
            origin_header="http://localhost:31800",
            db=Mock(),
            credential={"id": "credential"},
        )
    assert exc.value.code == "passkey_cloned"


@pytest.mark.asyncio
async def test_start_authentication_hides_unknown_username(monkeypatch):
    _rp_settings(monkeypatch)
    monkeypatch.setattr(
        "services.passkeys.ceremonies.User.get_by_username",
        AsyncMock(return_value=None),
    )
    store = AsyncMock()
    monkeypatch.setattr("services.passkeys.ceremonies.store_challenge", store)
    options = await start_authentication(
        origin_header="http://localhost:31800",
        db=Mock(),
        username="missing",
    )
    assert options["rpId"] == "localhost"
    assert options.get("allowCredentials") in ([], None)
    store.assert_awaited()
    assert store.await_args.kwargs["user_id"] is None
