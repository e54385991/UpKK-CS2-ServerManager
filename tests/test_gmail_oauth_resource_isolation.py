"""Per-application resource ownership for Gmail OAuth state."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.application import create_app
from api.routes import gmail_oauth
from modules.config import settings as default_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _OneTimeClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.values: dict[str, str] = {}
        self.fail = fail
        self.eval_calls = 0

    async def set(self, key: str, value: str, **options: Any) -> bool:
        if self.fail:
            raise ConnectionError("redis unavailable")
        if options.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, _script: str, _key_count: int, key: str) -> str | None:
        if self.fail:
            raise ConnectionError("redis unavailable")
        self.eval_calls += 1
        return self.values.pop(key, None)


class _RedisAdapter:
    def __init__(self, client: _OneTimeClient) -> None:
        self.client = client


def _create_isolated_app(*, backend_url: str, client: _OneTimeClient):
    app_settings = default_settings.model_copy(update={"BACKEND_URL": backend_url})
    database = SimpleNamespace(engine=object(), session_factory=lambda: None)
    return create_app(
        settings=app_settings,
        resource_overrides={
            "database": database,
            "redis": _RedisAdapter(client),
            "http": SimpleNamespace(),
            "ssh_pool": SimpleNamespace(),
        },
        lifespan=None,
    )


def _request(app, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "app": app,
        }
    )


@pytest.mark.asyncio
async def test_oauth_state_and_settings_are_isolated_between_factory_apps() -> None:
    first_client = _OneTimeClient()
    second_client = _OneTimeClient()
    first_app = _create_isolated_app(
        backend_url="https://first.example",
        client=first_client,
    )
    second_app = _create_isolated_app(
        backend_url="https://second.example/root/",
        client=second_client,
    )
    first_request = _request(first_app, "/api/gmail-oauth/callback")
    second_request = _request(second_app, "/api/gmail-oauth/callback")
    payload = {
        "admin_user_id": 42,
        "code_verifier": "verifier",
        "context_fingerprint": "fingerprint",
    }

    await gmail_oauth._store_oauth_state(
        gmail_oauth._oauth_state_redis(first_request),
        "state",
        payload,
    )

    assert (
        await gmail_oauth._consume_oauth_state(
            gmail_oauth._oauth_state_redis(second_request),
            "state",
        )
        is None
    )
    assert (
        await gmail_oauth._consume_oauth_state(
            gmail_oauth._oauth_state_redis(first_request),
            "state",
        )
        == payload
    )
    assert (
        await gmail_oauth._consume_oauth_state(
            gmail_oauth._oauth_state_redis(first_request),
            "state",
        )
        is None
    )
    assert gmail_oauth._oauth_redirect_uri(first_request) == (
        "https://first.example/api/gmail-oauth/callback"
    )
    assert gmail_oauth._oauth_redirect_uri(second_request) == (
        "https://second.example/root/api/gmail-oauth/callback"
    )
    assert second_client.eval_calls == 1
    assert first_client.eval_calls == 2


@pytest.mark.asyncio
async def test_callback_maps_missing_or_failed_app_redis_to_503() -> None:
    app = _create_isolated_app(
        backend_url="https://manager.example",
        client=_OneTimeClient(fail=True),
    )

    with pytest.raises(HTTPException) as failed:
        await gmail_oauth.gmail_oauth_callback(
            _request(app, "/api/gmail-oauth/callback"),
            code="code",
            state="state",
            db=SimpleNamespace(),
        )
    assert failed.value.status_code == 503

    app.state.container.redis = SimpleNamespace()
    with pytest.raises(HTTPException) as missing:
        await gmail_oauth.gmail_oauth_callback(
            _request(app, "/api/gmail-oauth/callback"),
            code="code",
            state="state",
            db=SimpleNamespace(),
        )
    assert missing.value.status_code == 503


def test_main_app_resolves_its_compatibility_redis_through_the_container() -> None:
    from services.redis_manager import redis_manager

    app = create_app(lifespan=None)
    request = _request(app, "/api/gmail-oauth/authorize")

    assert app.state.container.redis is redis_manager
    assert gmail_oauth._oauth_state_redis(request) is redis_manager


def test_gmail_oauth_openapi_contract_is_unchanged() -> None:
    app = _create_isolated_app(
        backend_url="https://manager.example",
        client=_OneTimeClient(),
    )
    current_paths = app.openapi()["paths"]
    with (PROJECT_ROOT / "tests" / "baselines" / "openapi.json").open(
        encoding="utf-8"
    ) as baseline_file:
        baseline_paths = json.load(baseline_file)["paths"]

    for path in (
        "/api/gmail-oauth/authorize",
        "/api/gmail-oauth/callback",
    ):
        assert current_paths[path] == baseline_paths[path]
