"""Zero-config Docker Hub compose and installer stay self-contained."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
DEBUG_COMPOSE = (PROJECT_ROOT / "docker-compose.debug.yml").read_text(encoding="utf-8")
QUICKSTART = (PROJECT_ROOT / "docker-quickstart.sh").read_text(encoding="utf-8")
README = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
README_ZH = (PROJECT_ROOT / "README.zh-CN.md").read_text(encoding="utf-8")


def test_release_compose_needs_no_repo_bind_mounts() -> None:
    assert "./deploy/" not in COMPOSE
    assert "INTERNAL_API_URL: ${FRONTEND_INTERNAL_API_URL:-http://app:8000}" in COMPOSE
    assert "PUBLIC_APP_URL" not in COMPOSE
    assert "${HTTP_PORT:-3000}:3000" in COMPOSE
    assert "upkk-cs2-server-manager:latest" in COMPOSE
    assert "upkk-cs2-server-manager-web:latest" in COMPOSE


def test_debug_compose_is_the_only_host_publish_for_api_and_db() -> None:
    assert "${API_PORT:-8000}:8000" in DEBUG_COMPOSE
    assert "${POSTGRES_PORT:-5432}:5432" in DEBUG_COMPOSE
    assert "${REDIS_PORT:-6379}:6379" in DEBUG_COMPOSE


def test_quickstart_installs_console_on_port_3000() -> None:
    assert "HTTP_PORT=3000" in QUICKSTART
    assert "PUBLIC_APP_URL" not in QUICKSTART
    assert "/login" in QUICKSTART
    assert "/health" in QUICKSTART
    assert "docker-compose.yml" in QUICKSTART


def test_readme_sends_operators_to_port_3000() -> None:
    assert "http://YOUR_SERVER_IP:3000" in README
    assert "http://你的服务器IP:3000" in README_ZH
    assert "TCP port `3000`" in README
    assert "TCP `3000`" in README_ZH
