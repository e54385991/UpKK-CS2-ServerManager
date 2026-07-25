"""Focused coverage for Gmail OAuth state and callback transaction safety."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from api.routes import gmail_oauth
from cs2_manager.infrastructure import credentials
from cs2_manager.infrastructure.credentials import CredentialCipher
from modules.config import settings as default_settings
from modules.schemas import GmailCredentialsUploadRequest


@pytest.fixture(autouse=True)
def _credential_cipher(monkeypatch):
    cipher = CredentialCipher({"test": bytes([19]) * 32}, "test")
    monkeypatch.setattr(credentials, "configured_cipher", lambda: cipher)


class NoopOAuthRedisClient:
    async def set(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    async def eval(self, *_args: Any) -> None:
        return None


class FailingOAuthRedisClient(NoopOAuthRedisClient):
    async def set(self, *_args: Any, **_kwargs: Any) -> bool:
        raise ConnectionError("redis unavailable")

    async def eval(self, *_args: Any) -> None:
        raise ConnectionError("redis unavailable")


def make_request(
    path: str,
    *,
    redis_client: Any | None = None,
    app_settings: Any | None = None,
) -> Request:
    redis = SimpleNamespace(client=redis_client or NoopOAuthRedisClient())
    app = SimpleNamespace(
        state=SimpleNamespace(
            container=SimpleNamespace(redis=redis),
            settings=app_settings or default_settings,
        )
    )
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "app": app,
        }
    )


def oauth_transaction(
    credentials_json: str = '{"web":{"client_id":"client"}}',
    token_json: str | None = None,
) -> dict[str, Any]:
    return {
        "admin_user_id": 42,
        "code_verifier": "pkce-verifier",
        "context_fingerprint": gmail_oauth._authorization_context_fingerprint(
            credentials_json,
            token_json,
        ),
    }


def assert_oauth_redirect(response: Response, result: str) -> None:
    assert response.status_code == 302
    assert response.headers["location"] == f"/system-settings?gmail_auth={result}"
    assert response.headers["cache-control"] == "no-store"


class OAuthDatabase:
    def __init__(self, *, user: Any = None, results: list[Any] | None = None) -> None:
        self.user = user
        self.results = list(results or [])
        self.statements: list[Any] = []
        self.commits = 0
        self.rollbacks = 0
        self.added: list[Any] = []

    async def get(self, _model: Any, _identifier: int) -> Any:
        return self.user

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        if not self.results:
            raise AssertionError("Unexpected database execute")
        return self.results.pop(0)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    def add(self, value: Any) -> None:
        self.added.append(value)


class RawSettingsResult:
    def __init__(self, row: tuple[bytes, bytes | None] | None) -> None:
        self.row = row

    def one_or_none(self) -> tuple[bytes, bytes | None] | None:
        return self.row


class UpdateResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class AuthorizeFlow:
    state = "oauth-state"
    verifier: str | None = "pkce-verifier"
    last_kwargs: dict[str, Any] | None = None

    def __init__(self) -> None:
        self.code_verifier = self.verifier

    @classmethod
    def from_client_config(cls, _credentials: dict[str, Any], **kwargs: Any) -> AuthorizeFlow:
        cls.last_kwargs = kwargs
        return cls()

    def authorization_url(self, **_kwargs: Any) -> tuple[str, str]:
        return f"https://accounts.example/authorize?state={self.state}", self.state


class CallbackFlow:
    last: CallbackFlow | None = None
    fail_fetch = False

    def __init__(self, credentials_info: dict[str, Any], kwargs: dict[str, Any]) -> None:
        self.credentials_info = credentials_info
        self.kwargs = kwargs
        self.fetch_codes: list[str] = []
        self.credentials = SimpleNamespace(
            token="access-token",
            refresh_token="refresh-token",
            token_uri="https://oauth.example/token",
            client_id="client-id",
            client_secret="client-secret",
            scopes=[gmail_oauth.GMAIL_OAUTH_SCOPE],
        )

    @classmethod
    def from_client_config(
        cls,
        credentials_info: dict[str, Any],
        **kwargs: Any,
    ) -> CallbackFlow:
        instance = cls(credentials_info, kwargs)
        cls.last = instance
        return instance

    def fetch_token(self, *, code: str) -> None:
        self.fetch_codes.append(code)
        if self.fail_fetch:
            raise RuntimeError("token exchange denied")


async def set_system_settings(monkeypatch: pytest.MonkeyPatch, value: Any) -> None:
    async def get_settings(_cls: Any, _db: Any) -> Any:
        return value

    monkeypatch.setattr(
        gmail_oauth.SystemSettings,
        "get_settings",
        classmethod(get_settings),
    )


async def set_or_create_system_settings(monkeypatch: pytest.MonkeyPatch, value: Any) -> None:
    async def get_settings(_cls: Any, _db: Any) -> Any:
        return value

    monkeypatch.setattr(
        gmail_oauth.SystemSettings,
        "get_or_create_settings",
        classmethod(get_settings),
    )


@pytest.mark.asyncio
async def test_state_reservation_collision_fails_closed() -> None:
    class CollisionRedis:
        async def set(self, *_args: Any, **_kwargs: Any) -> bool:
            return False

    redis = SimpleNamespace(client=CollisionRedis())

    with pytest.raises(
        gmail_oauth.OAuthStateStoreUnavailable,
        match="reserve a unique OAuth state",
    ):
        await gmail_oauth._store_oauth_state(redis, "duplicate", {"admin_user_id": 42})


@pytest.mark.asyncio
async def test_state_consumer_decodes_bytes_and_rejects_malformed_payloads() -> None:
    class StateRedis:
        def __init__(self) -> None:
            self.payloads: list[Any] = [
                b'{"admin_user_id":42}',
                b"not-json",
                json.dumps(["not", "a", "mapping"]),
            ]

        async def eval(self, *_args: Any) -> Any:
            return self.payloads.pop(0)

    redis = SimpleNamespace(client=StateRedis())

    assert await gmail_oauth._consume_oauth_state(redis, "bytes") == {"admin_user_id": 42}
    assert await gmail_oauth._consume_oauth_state(redis, "malformed") is None
    assert await gmail_oauth._consume_oauth_state(redis, "wrong-shape") is None


@pytest.mark.asyncio
async def test_authorize_rejects_missing_gmail_configuration(monkeypatch) -> None:
    await set_or_create_system_settings(
        monkeypatch,
        SimpleNamespace(gmail_credentials_json=None, gmail_token_json=None),
    )
    monkeypatch.setattr("google_auth_oauthlib.flow.Flow", AuthorizeFlow)
    db = OAuthDatabase()

    with pytest.raises(HTTPException) as caught:
        await gmail_oauth.gmail_oauth_authorize(
            make_request("/authorize"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            current_user=SimpleNamespace(id=42, is_admin=True),
        )

    assert caught.value.status_code == 400
    assert caught.value.detail == (
        "Gmail API credentials not configured. Please upload credentials JSON first."
    )
    assert db.commits == 0


@pytest.mark.parametrize(
    ("state", "verifier", "admin_id"),
    [
        ("", "pkce-verifier", 42),
        ("oauth-state", None, 42),
        ("oauth-state", "pkce-verifier", None),
    ],
)
@pytest.mark.asyncio
async def test_authorize_requires_state_pkce_and_persisted_admin(
    monkeypatch,
    state: str,
    verifier: str | None,
    admin_id: int | None,
) -> None:
    credentials_json = '{"web":{"client_id":"client"}}'
    await set_or_create_system_settings(
        monkeypatch,
        SimpleNamespace(gmail_credentials_json=credentials_json, gmail_token_json=None),
    )
    monkeypatch.setattr("google_auth_oauthlib.flow.Flow", AuthorizeFlow)
    monkeypatch.setattr(AuthorizeFlow, "state", state)
    monkeypatch.setattr(AuthorizeFlow, "verifier", verifier)
    store_state = AsyncMock()
    monkeypatch.setattr(gmail_oauth, "_store_oauth_state", store_state)
    db = OAuthDatabase()

    with pytest.raises(HTTPException) as caught:
        await gmail_oauth.gmail_oauth_authorize(
            make_request("/authorize"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            current_user=SimpleNamespace(id=admin_id, is_admin=True),
        )

    assert caught.value.status_code == 500
    assert caught.value.detail == (
        "OAuth provider did not create a secure authorization transaction"
    )
    store_state.assert_not_awaited()
    assert db.commits == 0


@pytest.mark.asyncio
async def test_authorize_maps_state_store_failure_to_503(monkeypatch) -> None:
    credentials_json = '{"web":{"client_id":"client"}}'
    await set_or_create_system_settings(
        monkeypatch,
        SimpleNamespace(gmail_credentials_json=credentials_json, gmail_token_json=None),
    )
    monkeypatch.setattr("google_auth_oauthlib.flow.Flow", AuthorizeFlow)
    monkeypatch.setattr(AuthorizeFlow, "state", "oauth-state")
    monkeypatch.setattr(AuthorizeFlow, "verifier", "pkce-verifier")
    db = OAuthDatabase()

    with pytest.raises(HTTPException) as caught:
        await gmail_oauth.gmail_oauth_authorize(
            make_request(
                "/authorize",
                redis_client=FailingOAuthRedisClient(),
            ),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            current_user=SimpleNamespace(id=42, is_admin=True),
        )

    assert caught.value.status_code == 503
    assert caught.value.detail == "OAuth authorization is temporarily unavailable"
    assert db.commits == 1


@pytest.mark.parametrize("state", [None, "s" * 513])
@pytest.mark.asyncio
async def test_callback_rejects_missing_or_oversized_state_before_consumption(
    monkeypatch,
    state: str | None,
) -> None:
    consume_state = AsyncMock()
    monkeypatch.setattr(gmail_oauth, "_consume_oauth_state", consume_state)

    response = await gmail_oauth.gmail_oauth_callback(
        make_request("/callback"),
        code="code",
        state=state,
        db=OAuthDatabase(),  # type: ignore[arg-type]
    )

    assert_oauth_redirect(response, "error")
    consume_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_rejects_expired_or_replayed_state(monkeypatch) -> None:
    consume_state = AsyncMock(return_value=None)
    monkeypatch.setattr(gmail_oauth, "_consume_oauth_state", consume_state)

    response = await gmail_oauth.gmail_oauth_callback(
        make_request("/callback"),
        code="code",
        state="expired-state",
        db=OAuthDatabase(),  # type: ignore[arg-type]
    )

    assert_oauth_redirect(response, "error")
    consume_state.assert_awaited_once()
    assert consume_state.await_args.args[1] == "expired-state"


@pytest.mark.parametrize(
    ("code", "error"),
    [(None, None), ("ignored-code", "access_denied")],
)
@pytest.mark.asyncio
async def test_callback_consumes_state_before_mapping_provider_errors(
    monkeypatch,
    code: str | None,
    error: str | None,
) -> None:
    consume_state = AsyncMock(return_value=oauth_transaction())
    monkeypatch.setattr(gmail_oauth, "_consume_oauth_state", consume_state)

    response = await gmail_oauth.gmail_oauth_callback(
        make_request("/callback"),
        code=code,
        state="one-time-state",
        error=error,
        db=OAuthDatabase(),  # type: ignore[arg-type]
    )

    assert_oauth_redirect(response, "error")
    consume_state.assert_awaited_once()
    assert consume_state.await_args.args[1] == "one-time-state"


@pytest.mark.asyncio
async def test_callback_rolls_back_malformed_transaction(monkeypatch) -> None:
    transaction = oauth_transaction()
    transaction["code_verifier"] = 123
    monkeypatch.setattr(gmail_oauth, "_consume_oauth_state", AsyncMock(return_value=transaction))
    db = OAuthDatabase()

    response = await gmail_oauth.gmail_oauth_callback(
        make_request("/callback"),
        code="code",
        state="state",
        db=db,  # type: ignore[arg-type]
    )

    assert_oauth_redirect(response, "error")
    assert db.rollbacks == 1
    assert db.commits == 0


@pytest.mark.parametrize(
    "settings_row",
    [
        None,
        SimpleNamespace(id=1, gmail_credentials_json=None, gmail_token_json=None),
        SimpleNamespace(
            id=None,
            gmail_credentials_json='{"web":{"client_id":"client"}}',
            gmail_token_json=None,
        ),
    ],
)
@pytest.mark.asyncio
async def test_callback_rejects_missing_or_incomplete_settings(monkeypatch, settings_row) -> None:
    monkeypatch.setattr(
        gmail_oauth,
        "_consume_oauth_state",
        AsyncMock(return_value=oauth_transaction()),
    )
    await set_system_settings(monkeypatch, settings_row)
    db = OAuthDatabase(user=SimpleNamespace(id=42, is_active=True, is_admin=True))

    response = await gmail_oauth.gmail_oauth_callback(
        make_request("/callback"),
        code="code",
        state="state",
        db=db,  # type: ignore[arg-type]
    )

    assert_oauth_redirect(response, "error")
    assert db.commits == 0
    assert db.rollbacks == 0


@pytest.mark.asyncio
async def test_callback_rejects_changed_authorization_context(monkeypatch) -> None:
    original_credentials = '{"web":{"client_id":"old"}}'
    current_credentials = '{"web":{"client_id":"new"}}'
    monkeypatch.setattr(
        gmail_oauth,
        "_consume_oauth_state",
        AsyncMock(return_value=oauth_transaction(original_credentials)),
    )
    await set_system_settings(
        monkeypatch,
        SimpleNamespace(
            id=1,
            gmail_credentials_json=current_credentials,
            gmail_token_json=None,
        ),
    )
    db = OAuthDatabase(user=SimpleNamespace(id=42, is_active=True, is_admin=True))

    response = await gmail_oauth.gmail_oauth_callback(
        make_request("/callback"),
        code="code",
        state="state",
        db=db,  # type: ignore[arg-type]
    )

    assert_oauth_redirect(response, "error")
    assert db.statements == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_callback_rejects_settings_row_deleted_before_exchange(monkeypatch) -> None:
    credentials_json = '{"web":{"client_id":"client"}}'
    monkeypatch.setattr(
        gmail_oauth,
        "_consume_oauth_state",
        AsyncMock(return_value=oauth_transaction(credentials_json)),
    )
    await set_system_settings(
        monkeypatch,
        SimpleNamespace(id=9, gmail_credentials_json=credentials_json, gmail_token_json=None),
    )
    db = OAuthDatabase(
        user=SimpleNamespace(id=42, is_active=True, is_admin=True),
        results=[RawSettingsResult(None)],
    )

    response = await gmail_oauth.gmail_oauth_callback(
        make_request("/callback"),
        code="code",
        state="state",
        db=db,  # type: ignore[arg-type]
    )

    assert_oauth_redirect(response, "error")
    assert len(db.statements) == 1
    assert db.commits == 0


def extract_update_token_json(statement: Any) -> str:
    for column, bind_parameter in statement._values.items():
        if getattr(column, "name", None) == "gmail_token_json":
            return bind_parameter.value
    raise AssertionError("gmail_token_json was not present in OAuth update")


@pytest.mark.parametrize("stored_token", [None, b"encrypted-existing-token"])
@pytest.mark.asyncio
async def test_callback_exchanges_token_and_conditionally_persists_credentials(
    monkeypatch,
    stored_token: bytes | None,
) -> None:
    credentials_json = '{"web":{"client_id":"client"}}'
    token_json = None if stored_token is None else '{"token":"old"}'
    transaction = oauth_transaction(credentials_json, token_json)
    monkeypatch.setattr(
        gmail_oauth,
        "_consume_oauth_state",
        AsyncMock(return_value=transaction),
    )
    await set_system_settings(
        monkeypatch,
        SimpleNamespace(
            id=9,
            gmail_credentials_json=credentials_json,
            gmail_token_json=token_json,
        ),
    )
    CallbackFlow.fail_fetch = False
    monkeypatch.setattr("google_auth_oauthlib.flow.Flow", CallbackFlow)
    db = OAuthDatabase(
        user=SimpleNamespace(id=42, is_active=True, is_admin=True),
        results=[
            RawSettingsResult((b"encrypted-credentials", stored_token)),
            UpdateResult(1),
        ],
    )

    response = await gmail_oauth.gmail_oauth_callback(
        make_request("/callback"),
        code="authorization-code",
        state="one-time-state",
        db=db,  # type: ignore[arg-type]
    )

    assert_oauth_redirect(response, "success")
    assert db.commits == 2
    assert db.rollbacks == 0
    assert len(db.statements) == 2
    assert CallbackFlow.last is not None
    assert CallbackFlow.last.fetch_codes == ["authorization-code"]
    assert CallbackFlow.last.kwargs == {
        "scopes": [gmail_oauth.GMAIL_OAUTH_SCOPE],
        "state": "one-time-state",
        "code_verifier": "pkce-verifier",
        "autogenerate_code_verifier": False,
        "redirect_uri": f"{default_settings.BACKEND_URL}/api/gmail-oauth/callback",
    }

    saved_token = json.loads(extract_update_token_json(db.statements[1]))
    assert saved_token == {
        "token": "access-token",
        "refresh_token": "refresh-token",
        "token_uri": "https://oauth.example/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": [gmail_oauth.GMAIL_OAUTH_SCOPE],
    }
    statement_text = str(db.statements[1])
    assert "gmail_credentials_json" in statement_text
    assert "gmail_token_json" in statement_text
    if stored_token is None:
        assert "IS NULL" in statement_text
    else:
        assert "IS NULL" not in statement_text


@pytest.mark.asyncio
async def test_callback_rolls_back_when_settings_change_during_token_exchange(monkeypatch) -> None:
    credentials_json = '{"web":{"client_id":"client"}}'
    monkeypatch.setattr(
        gmail_oauth,
        "_consume_oauth_state",
        AsyncMock(return_value=oauth_transaction(credentials_json)),
    )
    await set_system_settings(
        monkeypatch,
        SimpleNamespace(id=9, gmail_credentials_json=credentials_json, gmail_token_json=None),
    )
    CallbackFlow.fail_fetch = False
    monkeypatch.setattr("google_auth_oauthlib.flow.Flow", CallbackFlow)
    db = OAuthDatabase(
        user=SimpleNamespace(id=42, is_active=True, is_admin=True),
        results=[RawSettingsResult((b"encrypted-credentials", None)), UpdateResult(0)],
    )

    response = await gmail_oauth.gmail_oauth_callback(
        make_request("/callback"),
        code="authorization-code",
        state="state",
        db=db,  # type: ignore[arg-type]
    )

    assert_oauth_redirect(response, "error")
    assert db.commits == 1
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_callback_rolls_back_token_exchange_failure(monkeypatch) -> None:
    credentials_json = '{"web":{"client_id":"client"}}'
    monkeypatch.setattr(
        gmail_oauth,
        "_consume_oauth_state",
        AsyncMock(return_value=oauth_transaction(credentials_json)),
    )
    await set_system_settings(
        monkeypatch,
        SimpleNamespace(id=9, gmail_credentials_json=credentials_json, gmail_token_json=None),
    )
    CallbackFlow.fail_fetch = True
    monkeypatch.setattr("google_auth_oauthlib.flow.Flow", CallbackFlow)
    db = OAuthDatabase(
        user=SimpleNamespace(id=42, is_active=True, is_admin=True),
        results=[RawSettingsResult((b"encrypted-credentials", None))],
    )

    response = await gmail_oauth.gmail_oauth_callback(
        make_request("/callback"),
        code="authorization-code",
        state="state",
        db=db,  # type: ignore[arg-type]
    )

    assert_oauth_redirect(response, "error")
    assert db.commits == 1
    assert db.rollbacks == 1
    assert len(db.statements) == 1
    CallbackFlow.fail_fetch = False


@pytest.mark.asyncio
async def test_upload_credentials_preserves_validation_http_error() -> None:
    db = OAuthDatabase()
    request = GmailCredentialsUploadRequest(credentials_json='{"unexpected":true}')

    with pytest.raises(HTTPException) as caught:
        await gmail_oauth.upload_gmail_credentials(
            request,
            uow=SimpleNamespace(  # type: ignore[arg-type]
                session=db,
                commit=db.commit,
            ),
            current_user=SimpleNamespace(id=42, is_admin=True),
        )

    assert caught.value.status_code == 400
    assert "Google Cloud Console" in str(caught.value.detail)
    assert db.added == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_gmail_json_mutations_and_status_use_explicit_uow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = SimpleNamespace(id=42, is_admin=True)

    upload_settings = SimpleNamespace(
        gmail_credentials_json=None,
        gmail_token_json=None,
    )
    await set_system_settings(monkeypatch, upload_settings)
    upload_db = OAuthDatabase()
    upload_response = await gmail_oauth.upload_gmail_credentials(
        GmailCredentialsUploadRequest(
            credentials_json='{"web":{"client_id":"client"}}',
        ),
        uow=SimpleNamespace(  # type: ignore[arg-type]
            session=upload_db,
            commit=upload_db.commit,
        ),
        current_user=principal,
    )
    assert upload_response.model_dump() == {
        "success": True,
        "message": (
            "Gmail credentials uploaded successfully. You can now authorize the application."
        ),
    }
    assert upload_settings.gmail_credentials_json == '{"web":{"client_id":"client"}}'
    assert upload_db.commits == 1
    assert upload_db.added == [upload_settings]

    revoke_settings = SimpleNamespace(
        gmail_credentials_json='{"web":{}}',
        gmail_token_json='{"token":"secret"}',
    )
    await set_system_settings(monkeypatch, revoke_settings)
    revoke_db = OAuthDatabase()
    revoke_response = await gmail_oauth.revoke_gmail_authorization(
        uow=SimpleNamespace(  # type: ignore[arg-type]
            session=revoke_db,
            commit=revoke_db.commit,
        ),
        current_user=principal,
    )
    assert revoke_response.model_dump() == {
        "success": True,
        "message": "Gmail authorization revoked successfully",
    }
    assert revoke_settings.gmail_token_json is None
    assert revoke_db.commits == 1

    status_settings = SimpleNamespace(
        gmail_credentials_json='{"web":{}}',
        gmail_token_json='{"token":"secret"}',
    )
    await set_system_settings(monkeypatch, status_settings)
    status_db = OAuthDatabase()
    status_response = await gmail_oauth.gmail_oauth_status(
        uow=SimpleNamespace(  # type: ignore[arg-type]
            session=status_db,
            commit=status_db.commit,
        ),
        current_user=principal,
    )
    assert status_response.model_dump() == {
        "credentials_configured": True,
        "token_configured": True,
        "ready": True,
    }
    assert status_db.commits == 1


@pytest.mark.asyncio
async def test_gmail_status_creates_missing_settings_inside_uow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await set_system_settings(monkeypatch, None)
    db = OAuthDatabase()

    response = await gmail_oauth.gmail_oauth_status(
        uow=SimpleNamespace(  # type: ignore[arg-type]
            session=db,
            commit=db.commit,
        ),
        current_user=SimpleNamespace(id=42, is_admin=True),
    )

    assert response.model_dump() == {
        "credentials_configured": False,
        "token_configured": False,
        "ready": False,
    }
    assert len(db.added) == 1
    assert isinstance(db.added[0], gmail_oauth.SystemSettings)
    assert db.commits == 1
