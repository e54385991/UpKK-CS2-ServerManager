"""Regression checks for the 1Panel local application package."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_1panel_redis_password_is_required() -> None:
    import yaml

    version_data = yaml.safe_load(
        (PROJECT_ROOT / "deploy/1panel/apps/cs2-server-manager/1.0.0/data.yml").read_text(
            encoding="utf-8"
        )
    )
    fields = version_data["additionalProperties"]["formFields"]
    redis_password = next(
        item for item in fields if item.get("envKey") == "PANEL_REDIS_ROOT_PASSWORD"
    )
    assert redis_password["required"] is True
    assert redis_password.get("random") is not True
    compose = (
        PROJECT_ROOT / "deploy/1panel/apps/cs2-server-manager/1.0.0/docker-compose.yml"
    ).read_text(encoding="utf-8")
    assert "REDIS_PASSWORD: ${PANEL_REDIS_ROOT_PASSWORD}" in compose


def test_1panel_compose_isolates_second_instance() -> None:
    import yaml

    compose = (
        PROJECT_ROOT / "deploy/1panel/apps/cs2-server-manager/1.0.0/docker-compose.yml"
    ).read_text(encoding="utf-8")
    assert "INTERNAL_API_URL: http://${CONTAINER_NAME}:8000" in compose
    assert "REDIS_KEY_PREFIX: ${CONTAINER_NAME}" in compose
    assert "SESSION_COOKIE_SUFFIX: ${PANEL_APP_PORT_HTTP}" in compose
    assert "FRONTEND_UPSTREAM: ${CONTAINER_NAME}-web:3000" in compose
    assert 'API_PORT: "8000"' in compose
    assert "8001" not in compose

    version_data = yaml.safe_load(
        (PROJECT_ROOT / "deploy/1panel/apps/cs2-server-manager/1.0.0/data.yml").read_text(
            encoding="utf-8"
        )
    )
    internal_api = next(
        item
        for item in version_data["additionalProperties"]["formFields"]
        if item.get("envKey") == "FRONTEND_INTERNAL_API_URL"
    )
    assert internal_api.get("edit") is False
    assert internal_api.get("default") == "http://${CONTAINER_NAME}:8000"


def test_1panel_package_validator_passes() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_1panel_package.py"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_init_script_upgrades_short_secrets(tmp_path: Path) -> None:
    workdir = tmp_path / "app"
    workdir.mkdir(parents=True)
    (workdir / ".env").write_text(
        "SECRET_KEY=_short\nJWT_SECRET_KEY=operator-provided-key-that-is-long-enough\n",
        encoding="utf-8",
    )
    script = PROJECT_ROOT / "deploy/1panel/apps/cs2-server-manager/1.0.0/scripts/init.sh"
    # The real init script runs as root in 1Panel.  Mock only chown here so the
    # secret-rotation behavior can be tested on an unprivileged macOS runner.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_chown = fake_bin / "chown"
    fake_chown.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_chown.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        [str(script)], cwd=workdir, env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (workdir / "data").is_dir()
    values = dict(line.split("=", 1) for line in (workdir / ".env").read_text().splitlines())
    assert len(values["SECRET_KEY"]) == 64
    assert all(char in "0123456789abcdef" for char in values["SECRET_KEY"])
    assert values["JWT_SECRET_KEY"] == "operator-provided-key-that-is-long-enough"
