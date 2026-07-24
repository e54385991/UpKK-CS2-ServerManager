"""Steam API calls must use the outbound HTTP adapter owned by each app."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from api.dependencies import get_unit_of_work
from api.routes import auth
from cs2_manager.core import Principal
from modules.auth import get_current_principal
from services.steam_api_service import SteamAPIService, steam_api_service


class _SteamHTTP:
    def __init__(self, name: str, events: list[str] | None = None) -> None:
        self.name = name
        self.events = events
        self.is_closed = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, url: str, **kwargs: Any):
        self.calls.append((url, kwargs))
        return (
            True,
            {
                "response": {
                    "success": True,
                    "up_to_date": False,
                    "message": f"Server version required: 1.0.0.{self.name}",
                }
            },
            None,
        )

    async def post(self, url: str, **kwargs: Any):
        if self.events is not None:
            assert self.events == ["commit"]
            self.events.append("steam")
        self.calls.append((url, kwargs))
        return (
            True,
            {
                "response": {
                    "login_token": f"token-{self.name}",
                    "steamid": f"steam-{self.name}",
                }
            },
            None,
        )

    @asynccontextmanager
    async def borrow_client(self):
        yield SimpleNamespace()


class _SteamResponseHTTP(_SteamHTTP):
    def __init__(self, response_data: object) -> None:
        super().__init__("response")
        self.response_data = response_data

    async def post(self, url: str, **kwargs: Any):
        self.calls.append((url, kwargs))
        return True, self.response_data, None


def _uow(
    *,
    username: str = "owner",
    steam_api_key: str | None = "a" * 32,
    events: list[str] | None = None,
):
    user = SimpleNamespace(
        username=username,
        steam_api_key=steam_api_key,
    )

    async def commit() -> None:
        if events is not None:
            events.append("commit")

    return SimpleNamespace(
        session=SimpleNamespace(get=AsyncMock(return_value=user)),
        commit=AsyncMock(side_effect=commit),
    )


def _app(http_resource: object, uow: object) -> FastAPI:
    app = FastAPI()
    app.state.container = SimpleNamespace(http=http_resource)
    app.include_router(auth.router)
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        id=7,
        username="principal-name",
        email="owner@example.com",
    )
    app.dependency_overrides[get_unit_of_work] = lambda: uow
    return app


@pytest.mark.asyncio
async def test_two_apps_use_only_their_steam_http_adapter_after_db_commit(monkeypatch) -> None:
    monkeypatch.setattr(auth.captcha_service, "validate_captcha", AsyncMock(return_value=True))

    async def forbid_global(*_args: Any, **_kwargs: Any):
        raise AssertionError("the process-global Steam HTTP facade must not be used")

    monkeypatch.setattr(steam_api_service.http_adapter, "post", forbid_global)

    first_events: list[str] = []
    second_events: list[str] = []
    first_http = _SteamHTTP("first", first_events)
    second_http = _SteamHTTP("second", second_events)
    first_uow = _uow(username="first-owner", events=first_events)
    second_uow = _uow(username="second-owner", events=second_events)
    first_app = _app(first_http, first_uow)
    second_app = _app(second_http, second_uow)

    payload = {
        "server_name": "",
        "captcha_token": "captcha",
        "captcha_code": "1234",
    }
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=first_app),
            base_url="http://first",
        ) as first_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=second_app),
            base_url="http://second",
        ) as second_client,
    ):
        first_response = await first_client.post(
            "/api/auth/generate-server-token",
            json=payload,
        )
        second_response = await second_client.post(
            "/api/auth/generate-server-token",
            json=payload,
        )

    assert first_response.status_code == second_response.status_code == 200
    assert first_response.json() == {
        "success": True,
        "login_token": "token-first",
        "error": None,
    }
    assert second_response.json() == {
        "success": True,
        "login_token": "token-second",
        "error": None,
    }
    assert first_response.headers["cache-control"] == "no-store"
    assert second_response.headers["cache-control"] == "no-store"
    assert first_events == ["commit", "steam"]
    assert second_events == ["commit", "steam"]
    first_uow.commit.assert_awaited_once_with()
    second_uow.commit.assert_awaited_once_with()
    assert first_http.calls[0][1]["params"]["memo"] == "CS2 Server - first-owner"
    assert second_http.calls[0][1]["params"]["memo"] == "CS2 Server - second-owner"


@pytest.mark.asyncio
@pytest.mark.parametrize("resource_kind", ("missing", "closed"))
async def test_generate_token_fails_closed_for_missing_or_closed_http(
    monkeypatch,
    resource_kind: str,
) -> None:
    captcha = AsyncMock(return_value=True)
    monkeypatch.setattr(auth.captcha_service, "validate_captcha", captcha)
    resource: object = None
    if resource_kind == "closed":
        closed = _SteamHTTP("closed")
        closed.is_closed = True
        resource = closed
    uow = _uow()
    app = _app(resource, uow)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/auth/generate-server-token",
            json={
                "captcha_token": "captcha",
                "captcha_code": "1234",
            },
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Outbound HTTP client is unavailable"}
    captcha.assert_not_awaited()
    uow.session.get.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_token_releases_database_before_reporting_missing_key(monkeypatch) -> None:
    monkeypatch.setattr(auth.captcha_service, "validate_captcha", AsyncMock(return_value=True))
    outbound_http = _SteamHTTP("unused")
    uow = _uow(steam_api_key=None)
    app = _app(outbound_http, uow)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/auth/generate-server-token",
            json={
                "captcha_token": "captcha",
                "captcha_code": "1234",
            },
        )

    assert response.status_code == 400
    assert response.json() == {
        "detail": (
            "Steam API key not set. Please set your Steam API key in profile settings first."
        )
    }
    uow.commit.assert_awaited_once_with()
    assert outbound_http.calls == []


@pytest.mark.asyncio
async def test_generate_token_preserves_steam_failure_response_contract(monkeypatch) -> None:
    monkeypatch.setattr(auth.captcha_service, "validate_captcha", AsyncMock(return_value=True))
    outbound_http = _SteamResponseHTTP({"response": {"error": "quota exceeded"}})
    uow = _uow()
    app = _app(outbound_http, uow)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/auth/generate-server-token",
            json={
                "server_name": " Dedicated server ",
                "captcha_token": "captcha",
                "captcha_code": "1234",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "login_token": None,
        "error": "Failed to generate token: quota exceeded",
    }
    uow.commit.assert_awaited_once_with()
    assert outbound_http.calls[0][1]["params"]["memo"] == "Dedicated server"


@pytest.mark.asyncio
async def test_generate_token_maps_disappeared_user_to_authenticated_error(monkeypatch) -> None:
    monkeypatch.setattr(auth.captcha_service, "validate_captcha", AsyncMock(return_value=True))
    outbound_http = _SteamHTTP("unused")
    uow = _uow()
    uow.session.get.return_value = None
    app = _app(outbound_http, uow)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/auth/generate-server-token",
            json={
                "captcha_token": "captcha",
                "captcha_code": "1234",
            },
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "User not found"}
    assert response.headers["www-authenticate"] == "Bearer"
    uow.commit.assert_not_awaited()
    assert outbound_http.calls == []


@pytest.mark.asyncio
async def test_steam_service_uses_its_explicit_adapter_for_version_checks() -> None:
    outbound_http = _SteamHTTP("42")

    success, result = await SteamAPIService(http_adapter=outbound_http).check_version("1.0.0.1")

    assert success is True
    assert result is not None
    assert result["required_version"] == "1.0.0.42"
    assert outbound_http.calls[0][0] == SteamAPIService.VERSION_CHECK_URL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_data", "expected_error"),
    (
        (None, "Unexpected API response format"),
        ({"response": []}, "Invalid API response structure"),
    ),
)
async def test_steam_service_rejects_malformed_account_responses(
    response_data: object,
    expected_error: str,
) -> None:
    success, result = await SteamAPIService(
        http_adapter=_SteamResponseHTTP(response_data)
    ).create_game_server_account("a" * 32)

    assert success is False
    assert result == {"success": False, "error": expected_error}


def test_default_steam_facade_retains_non_asgi_compatibility() -> None:
    assert SteamAPIService().http_adapter is steam_api_service.http_adapter


def test_generate_token_openapi_declares_success_security_and_errors() -> None:
    operation = _app(_SteamHTTP("schema"), _uow()).openapi()["paths"][
        "/api/auth/generate-server-token"
    ]["post"]

    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/GenerateServerTokenResponse"
    }
    for status_code in ("400", "401", "503"):
        assert operation["responses"][status_code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
    assert operation["security"] == [{"OAuth2PasswordBearer": []}]
