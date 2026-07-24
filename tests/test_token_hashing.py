"""Security coverage for non-recoverable user and reset tokens."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import Response
from starlette.requests import Request

from api.routes import auth as auth_routes
from modules.config import settings
from modules.models.identity import PasswordResetToken, User
from modules.schemas.auth import ApiKeyGenerate


class _Session:
    def __init__(self) -> None:
        self.added = None
        self.commits = 0

    def add(self, value) -> None:
        self.added = value

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _value) -> None:
        return None


def _user() -> User:
    return User(
        id=7,
        username="operator",
        email="operator@example.test",
        hashed_password="hash",
        updated_at=datetime.now(UTC),
    )


def test_user_api_key_is_stored_as_hmac_only(monkeypatch):
    monkeypatch.setattr(settings, "TOKEN_HASH_KEY", "token-hash-key" * 4)
    user = _user()

    user.set_api_key("A" * 64)

    assert user.api_key is None
    assert user.api_key_hash
    assert user.api_key_hash != "A" * 64
    assert user.api_key_prefix == "A" * 8
    assert user.has_api_key is True

    user.set_api_key(None)
    assert user.api_key_hash is None
    assert user.api_key_prefix is None
    assert user.has_api_key is False


@pytest.mark.asyncio
async def test_password_reset_token_is_stored_as_hmac_only(monkeypatch):
    monkeypatch.setattr(settings, "TOKEN_HASH_KEY", "token-hash-key" * 4)
    session = _Session()
    plaintext = "R" * 64

    created = await PasswordResetToken.create_token(
        session, 7, plaintext, datetime.now(UTC) + timedelta(hours=1)
    )

    assert created is session.added
    assert created.token is None
    assert created.token_hash
    assert created.token_hash != plaintext
    assert created.token_prefix == plaintext[:8]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_api_key_creation_is_one_time_and_non_cacheable(monkeypatch):
    monkeypatch.setattr(settings, "TOKEN_HASH_KEY", "token-hash-key" * 4)
    plaintext = "K" * 64

    async def no_existing_key(_cls, _session, _api_key):
        return None

    monkeypatch.setattr(User, "get_by_api_key", classmethod(no_existing_key))
    monkeypatch.setattr(auth_routes, "generate_api_key", lambda: plaintext)

    user = _user()
    session = _Session()
    response = Response()
    result = await auth_routes.generate_user_api_key(ApiKeyGenerate(), response, user, session)

    assert result["api_key"] == plaintext
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert user.api_key is None
    assert user.api_key_hash


@pytest.mark.asyncio
async def test_api_key_status_never_echoes_plaintext():
    user = _user()
    user.api_key = "legacy-secret-api-key"

    result = await auth_routes.get_api_key(user)

    assert result["configured"] is True
    assert result["prefix"] == "legacy-s"
    assert "api_key" not in result


def test_open_registration_defaults_closed_in_production():
    production = settings.model_copy(
        update={"ENVIRONMENT": "production", "ALLOW_REGISTRATION": None}
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/register",
            "headers": [],
            "app": SimpleNamespace(state=SimpleNamespace(settings=production)),
        }
    )

    assert production.registration_enabled is False
    assert auth_routes._registration_enabled(request) is False
    assert production.model_copy(update={"ALLOW_REGISTRATION": True}).registration_enabled is True
