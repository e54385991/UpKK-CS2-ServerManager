"""CAPTCHA challenge JSON used by the Next login page on LAN hosts."""

import base64
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.application import create_app


def test_captcha_challenge_returns_inline_image(monkeypatch):
    monkeypatch.setattr(
        "api.routes.captcha.enforce_rate_limit",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.captcha.captcha_service.generate_captcha",
        AsyncMock(return_value=("tok-lan", b"\x89PNG-fake")),
    )
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/captcha/challenge")
    assert response.status_code == 200
    body = response.json()
    assert body["token"] == "tok-lan"
    assert body["image"].startswith("data:image/png;base64,")
    assert body["enabled"] is True
    payload = body["image"].split(",", 1)[1]
    assert base64.b64decode(payload) == b"\x89PNG-fake"


def test_captcha_challenge_is_unauthenticated(monkeypatch):
    """The login page must be able to load a challenge before a session exists."""
    # Stub the Redis-backed limiter and store so the result does not depend on
    # a live Redis or on pooled connections left bound to another test's loop.
    monkeypatch.setattr(
        "api.routes.captcha.enforce_rate_limit",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.captcha.captcha_is_enabled",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "api.routes.captcha.captcha_service.generate_captcha",
        AsyncMock(return_value=("tok-public", b"\x89PNG-fake")),
    )

    # No session cookie or bearer token is sent: the route must stay public.
    response = TestClient(create_app(lifespan=None)).get("/api/captcha/challenge")

    assert response.status_code == 200
    assert response.json()["token"] == "tok-public"
    assert response.json()["image"].startswith("data:image/png;base64,")


def test_captcha_challenge_reports_disabled_policy(monkeypatch):
    monkeypatch.setattr(
        "api.routes.captcha.enforce_rate_limit",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.captcha.captcha_is_enabled",
        AsyncMock(return_value=False),
    )
    generate = AsyncMock()
    monkeypatch.setattr("api.routes.captcha.captcha_service.generate_captcha", generate)

    response = TestClient(create_app(lifespan=None)).get("/api/captcha/challenge")

    assert response.status_code == 200
    assert response.json() == {"token": "", "image": "", "enabled": False}
    generate.assert_not_awaited()
