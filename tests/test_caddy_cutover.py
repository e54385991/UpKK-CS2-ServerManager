"""Public-root cutover: Caddy → Next, FastAPI stays private."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
CADDYFILE = (PROJECT_ROOT / "deploy/Caddyfile").read_text(encoding="utf-8")
CADDYFILE_HOST = (PROJECT_ROOT / "deploy/Caddyfile.host").read_text(encoding="utf-8")
PANEL_COMPOSE = (
    PROJECT_ROOT / "deploy/1panel/apps/cs2-server-manager/1.0.0/docker-compose.yml"
).read_text(encoding="utf-8")
PANEL_CADDY = (PROJECT_ROOT / "deploy/1panel/apps/cs2-server-manager/1.0.0/Caddyfile").read_text(
    encoding="utf-8"
)


def test_root_caddyfile_proxies_next() -> None:
    assert "reverse_proxy frontend:3000" in CADDYFILE
    assert "reverse_proxy app:8000" not in CADDYFILE


def test_host_caddyfile_proxies_next_on_the_developer_machine() -> None:
    assert "reverse_proxy host.docker.internal:3000" in CADDYFILE_HOST


def test_root_compose_publishes_caddy_not_fastapi_as_http() -> None:
    assert "${HTTP_PORT:-80}:80" in COMPOSE
    assert "./deploy/Caddyfile:/etc/caddy/Caddyfile:ro" in COMPOSE
    assert "upkk-cs2-server-manager-web" in COMPOSE
    assert "${API_PORT:-8000}:8000" in COMPOSE


def test_1panel_compose_publishes_caddy_as_public_root() -> None:
    assert "${PANEL_APP_PORT_HTTP}:80" in PANEL_COMPOSE
    assert "${PANEL_APP_PORT_HTTP}:8000" not in PANEL_COMPOSE
    assert "reverse_proxy frontend:3000" in PANEL_CADDY
    assert "INTERNAL_API_URL: http://app:8000" in PANEL_COMPOSE
    assert 'expose:\n      - "8000"' in PANEL_COMPOSE
