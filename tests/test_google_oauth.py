"""Coverage for the stored vs environment Google OAuth client ID and ID-token checks."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.google_oauth import (
    GoogleIdentityError,
    effective_google_client_id,
    stored_google_client_id,
    verify_google_id_token,
)


def test_effective_google_client_id_prefers_stored_value(monkeypatch):
    monkeypatch.setattr(
        "services.google_oauth.environment_google_client_id",
        lambda: "env-client.apps.googleusercontent.com",
    )
    settings = SimpleNamespace(google_client_id="  db-client.apps.googleusercontent.com  ")
    assert stored_google_client_id(settings) == "db-client.apps.googleusercontent.com"
    assert effective_google_client_id(settings) == "db-client.apps.googleusercontent.com"


def test_effective_google_client_id_falls_back_to_environment(monkeypatch):
    monkeypatch.setattr(
        "services.google_oauth.environment_google_client_id",
        lambda: "env-client.apps.googleusercontent.com",
    )
    assert effective_google_client_id(None) == "env-client.apps.googleusercontent.com"
    assert effective_google_client_id(SimpleNamespace(google_client_id="")) == (
        "env-client.apps.googleusercontent.com"
    )


async def test_verify_google_id_token_requires_a_configured_client(monkeypatch):
    monkeypatch.setattr(
        "services.google_oauth.resolve_google_client_id",
        AsyncMock(return_value=""),
    )
    with pytest.raises(GoogleIdentityError) as caught:
        await verify_google_id_token(SimpleNamespace(), "raw-token")
    assert caught.value.code == "google_not_configured"


async def test_verify_google_id_token_accepts_a_signed_subject(monkeypatch):
    monkeypatch.setattr(
        "services.google_oauth.resolve_google_client_id",
        AsyncMock(return_value="client.apps.googleusercontent.com"),
    )
    payload = {"sub": "subject-1", "email_verified": True}
    monkeypatch.setattr(
        "services.google_oauth.to_thread.run_sync",
        AsyncMock(return_value=payload),
    )
    assert await verify_google_id_token(SimpleNamespace(), "raw-token") is payload


async def test_verify_google_id_token_rejects_bad_tokens(monkeypatch):
    monkeypatch.setattr(
        "services.google_oauth.resolve_google_client_id",
        AsyncMock(return_value="client.apps.googleusercontent.com"),
    )
    verifier = AsyncMock(side_effect=ValueError("bad signature"))
    monkeypatch.setattr("services.google_oauth.to_thread.run_sync", verifier)
    with pytest.raises(GoogleIdentityError) as caught:
        await verify_google_id_token(SimpleNamespace(), "raw-token")
    assert caught.value.code == "google_invalid_token"

    verifier.side_effect = None
    for payload in ("nope", {"email": "a@b.c"}, {"sub": "  "}):
        verifier.return_value = payload
        with pytest.raises(GoogleIdentityError) as caught:
            await verify_google_id_token(SimpleNamespace(), "raw-token")
        assert caught.value.code == "google_invalid_token"
