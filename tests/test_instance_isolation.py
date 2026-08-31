"""Two 1Panel installs can share Redis DB 0 and one host without 401 after login."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.responses import Response

from modules.auth import (
    WEB_SESSION_COOKIE,
    clear_web_session_cookie,
    set_web_session_cookie,
    web_session_cookie_name,
)
from modules.config import settings
from services.redis_manager import redis_manager

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PANEL_COMPOSE = (
    PROJECT_ROOT / "deploy/1panel/apps/cs2-server-manager/1.0.0/docker-compose.yml"
).read_text(encoding="utf-8")


def _http_request() -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"spec_version": "2.3", "version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 123),
            "server": ("test", 80),
        }
    )


def test_web_session_cookie_defaults_to_shared_name(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SESSION_COOKIE_SUFFIX", "")
    assert web_session_cookie_name() == WEB_SESSION_COOKIE


def test_web_session_cookie_name_includes_public_port(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SESSION_COOKIE_SUFFIX", "3001")
    assert web_session_cookie_name() == "upkk_access_token_3001"


def test_two_console_ports_do_not_share_a_session_cookie(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SESSION_COOKIE_SUFFIX", "3000")
    first = web_session_cookie_name()
    monkeypatch.setattr(settings, "SESSION_COOKIE_SUFFIX", "3001")
    second = web_session_cookie_name()
    assert first == "upkk_access_token_3000"
    assert second == "upkk_access_token_3001"
    assert first != second
    assert "8000" not in first
    assert "8001" not in second


def test_set_and_clear_web_session_cookie_use_port_suffix(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SESSION_COOKIE_SUFFIX", "3001")
    request = _http_request()
    response = Response()
    set_web_session_cookie(request, response, "token-value")
    header = response.headers.get("set-cookie")
    assert header is not None
    assert header.startswith("upkk_access_token_3001=")
    assert "upkk_access_token=" not in header.replace("upkk_access_token_3001", "COOKIE")

    cleared = Response()
    clear_web_session_cookie(cleared)
    assert "upkk_access_token_3001=" in (cleared.headers.get("set-cookie") or "")


def test_prefixed_key_empty_prefix_is_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(settings, "REDIS_KEY_PREFIX", "")
    assert redis_manager.prefixed_key("captcha:abc") == "captcha:abc"


def test_prefixed_key_namespaces_same_db_keys(monkeypatch) -> None:
    monkeypatch.setattr(settings, "REDIS_KEY_PREFIX", "cs2-a")
    assert redis_manager.prefixed_key("captcha:abc") == "cs2-a:captcha:abc"
    assert redis_manager.prefixed_key("cs2-a:captcha:abc") == "cs2-a:captcha:abc"
    monkeypatch.setattr(settings, "REDIS_KEY_PREFIX", "cs2-b")
    assert redis_manager.prefixed_key("captcha:abc") == "cs2-b:captcha:abc"
    assert redis_manager.prefixed_key("server_op_current:1") == "cs2-b:server_op_current:1"


@pytest.mark.asyncio
async def test_redis_manager_set_writes_prefixed_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "REDIS_KEY_PREFIX", "inst-a")
    captured: dict[str, object] = {}

    async def fake_setex(key, expire, value):
        captured["key"] = key
        captured["expire"] = expire
        captured["value"] = value
        return True

    monkeypatch.setattr(redis_manager.client, "setex", fake_setex)
    assert await redis_manager.set("captcha:tok", "AB12", expire=30) is True
    assert captured["key"] == "inst-a:captcha:tok"
    assert captured["value"] == "AB12"


def test_1panel_compose_isolates_shared_network_and_redis() -> None:
    assert "INTERNAL_API_URL: http://${CONTAINER_NAME}:8000" in PANEL_COMPOSE
    assert "INTERNAL_API_URL: ${FRONTEND_INTERNAL_API_URL}" not in PANEL_COMPOSE
    assert "REDIS_KEY_PREFIX: ${CONTAINER_NAME}" in PANEL_COMPOSE
    assert "SESSION_COOKIE_SUFFIX: ${PANEL_APP_PORT_HTTP}" in PANEL_COMPOSE
    assert "FRONTEND_UPSTREAM: ${CONTAINER_NAME}-web:3000" in PANEL_COMPOSE
    assert 'API_PORT: "8000"' in PANEL_COMPOSE
    assert 'expose:\n      - "8000"' in PANEL_COMPOSE
    assert "${PANEL_APP_PORT_HTTP}:80" in PANEL_COMPOSE
    assert "${PANEL_APP_PORT_HTTP}:8000" not in PANEL_COMPOSE
    assert "8001" not in PANEL_COMPOSE
    assert "SESSION_COOKIE_SUFFIX: ${API_PORT}" not in PANEL_COMPOSE
    assert "SESSION_COOKIE_SUFFIX: 8001" not in PANEL_COMPOSE


def test_frontend_session_cookie_matches_this_instance_only() -> None:
    text = (PROJECT_ROOT / "frontend/src/modules/auth/session.ts").read_text(encoding="utf-8")
    assert 'process.env["SESSION_COOKIE_SUFFIX"]' in text
    assert "store.has(sessionCookieName())" in text
    assert "startsWith(`${SESSION_COOKIE}_`)" not in text
