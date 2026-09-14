"""Coverage for the stored vs environment Google OAuth client ID."""

from __future__ import annotations

from types import SimpleNamespace

from services.google_oauth import (
    effective_google_client_id,
    stored_google_client_id,
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
