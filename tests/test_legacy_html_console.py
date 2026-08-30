"""Leftover Jinja pages redirect to the Next.js origin by default."""

from fastapi.testclient import TestClient

from api.application import create_app
from modules import settings


def _client() -> TestClient:
    return TestClient(create_app(lifespan=None))


def test_root_and_login_redirect_to_next_console(monkeypatch):
    monkeypatch.setattr(settings, "LEGACY_HTML_CONSOLE", "redirect")
    monkeypatch.setattr(settings, "CONSOLE_PUBLIC_URL", "http://console.test")
    client = _client()

    root = client.get("/", follow_redirects=False)
    assert root.status_code == 307
    assert root.headers["location"] == "http://console.test/overview"

    login = client.get("/login?next=/servers", follow_redirects=False)
    assert login.status_code == 307
    assert login.headers["location"] == "http://console.test/login?next=/servers"

    register = client.get("/register?next=/servers", follow_redirects=False)
    assert register.status_code == 307
    assert register.headers["location"] == "http://console.test/register?next=/servers"

    servers = client.get("/servers-ui", follow_redirects=False)
    assert servers.status_code == 307
    assert servers.headers["location"] == "http://console.test/servers"

    forgot = client.get("/forgot-password", follow_redirects=False)
    assert forgot.status_code == 307
    assert forgot.headers["location"] == "http://console.test/forgot-password"

    reset = client.get("/reset-password?token=abc", follow_redirects=False)
    assert reset.status_code == 307
    assert reset.headers["location"] == "http://console.test/reset-password?token=abc"

    tutorial = client.get("/deployment-tutorial", follow_redirects=False)
    assert tutorial.status_code == 307
    assert tutorial.headers["location"] == "http://console.test/deployment-tutorial"


def test_auth_gated_legacy_pages_redirect_before_fastapi_login(monkeypatch):
    monkeypatch.setattr(settings, "LEGACY_HTML_CONSOLE", "redirect")
    monkeypatch.setattr(settings, "CONSOLE_PUBLIC_URL", "http://console.test")
    client = _client()

    detail = client.get("/servers-ui/4", follow_redirects=False)
    assert detail.status_code == 307
    assert detail.headers["location"] == "http://console.test/servers/4"

    market = client.get("/plugin-market", follow_redirects=False)
    assert market.status_code == 307
    assert market.headers["location"] == "http://console.test/plugins"

    profile = client.get("/profile", follow_redirects=False)
    assert profile.status_code == 307
    assert profile.headers["location"] == "http://console.test/settings/profile"

    files = client.get(
        "/servers/4/file-editor-popup?file_path=cs2/game/csgo/cfg/server.cfg&file_name=server.cfg",
        follow_redirects=False,
    )
    assert files.status_code == 307
    assert (
        files.headers["location"]
        == "http://console.test/servers/4/files?path=cs2%2Fgame%2Fcsgo%2Fcfg%2Fserver.cfg"
    )


def test_legacy_html_can_still_serve_jinja(monkeypatch):
    monkeypatch.setattr(settings, "LEGACY_HTML_CONSOLE", "serve")
    client = _client()
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert b"<html" in response.content.lower() or b"<!doctype" in response.content.lower()


def test_legacy_html_can_return_gone(monkeypatch):
    monkeypatch.setattr(settings, "LEGACY_HTML_CONSOLE", "gone")
    client = _client()
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 404
    assert "Next.js" in response.json()["detail"]


def test_google_callback_stays_on_fastapi(monkeypatch):
    monkeypatch.setattr(settings, "LEGACY_HTML_CONSOLE", "redirect")
    client = _client()
    response = client.get("/google-callback", follow_redirects=False)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_loopback_console_url_follows_lan_host(monkeypatch):
    monkeypatch.setattr(settings, "LEGACY_HTML_CONSOLE", "redirect")
    monkeypatch.setattr(settings, "CONSOLE_PUBLIC_URL", "http://localhost:3000")
    monkeypatch.setattr(settings, "API_PORT", 8000)
    client = _client()

    from_api = client.get(
        "/",
        follow_redirects=False,
        headers={"host": "192.168.50.143:8000"},
    )
    assert from_api.status_code == 307
    assert from_api.headers["location"] == "http://192.168.50.143:3000/overview"
    assert "127.0.0.1" not in from_api.headers["location"]
    assert "localhost" not in from_api.headers["location"]

    from_console = client.get(
        "/login",
        follow_redirects=False,
        headers={"host": "192.168.50.143:3000"},
    )
    assert from_console.headers["location"] == "http://192.168.50.143:3000/login"


def test_explicit_console_url_still_wins(monkeypatch):
    monkeypatch.setattr(settings, "LEGACY_HTML_CONSOLE", "redirect")
    monkeypatch.setattr(settings, "CONSOLE_PUBLIC_URL", "http://console.test")
    client = _client()
    response = client.get(
        "/",
        follow_redirects=False,
        headers={"host": "192.168.50.143:8000"},
    )
    assert response.headers["location"] == "http://console.test/overview"


def test_api_and_health_are_not_redirected(monkeypatch):
    monkeypatch.setattr(settings, "LEGACY_HTML_CONSOLE", "redirect")
    monkeypatch.setattr(settings, "CONSOLE_PUBLIC_URL", "http://console.test")
    client = _client()
    health = client.get("/health")
    assert health.status_code == 200
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 401
    assert "console.test" not in me.headers.get("location", "")
