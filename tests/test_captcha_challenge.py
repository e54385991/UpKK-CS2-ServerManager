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
    payload = body["image"].split(",", 1)[1]
    assert base64.b64decode(payload) == b"\x89PNG-fake"


def test_captcha_challenge_is_unauthenticated():
    """The login page must be able to load a challenge before a session exists."""
    client = TestClient(create_app(lifespan=None))
    # Rate limiter / Redis may be live; only assert the route is public.
    response = client.get("/api/captcha/challenge")
    assert response.status_code in {200, 429}
    if response.status_code == 200:
        assert "token" in response.json()
        assert response.json()["image"].startswith("data:image/png;base64,")
