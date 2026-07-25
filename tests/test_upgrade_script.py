"""Source-upgrade environment preparation without a real database or server."""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

from scripts.prepare_env import EnvironmentPreparationError, prepare_environment

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _decoded_key(encoded: str) -> bytes:
    return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))


def test_prepare_environment_creates_secure_complete_idempotent_file(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    changed = prepare_environment(env_path, PROJECT_ROOT / ".env.example")
    first_content = env_path.read_text(encoding="utf-8")
    values = dotenv_values(env_path)

    assert ".env" in changed
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert values["SECRET_KEY"] != "your-secret-key-change-this-in-production"
    assert values["JWT_SECRET_KEY"] != "your-jwt-secret-key-change-this-in-production"
    assert len(values["SECRET_KEY"] or "") >= 48
    assert len(values["JWT_SECRET_KEY"] or "") >= 48
    assert len(values["TOKEN_HASH_KEY"] or "") >= 48
    assert (
        len(
            {
                values["SECRET_KEY"],
                values["JWT_SECRET_KEY"],
                values["TOKEN_HASH_KEY"],
            }
        )
        == 3
    )

    keyring = json.loads(values["CREDENTIAL_ENCRYPTION_KEYS"] or "")
    active_key_id = values["CREDENTIAL_ACTIVE_KEY_ID"]
    assert active_key_id in keyring
    assert len(_decoded_key(keyring[active_key_id])) == 32

    assert prepare_environment(env_path, PROJECT_ROOT / ".env.example") == []
    assert env_path.read_text(encoding="utf-8") == first_content


def test_prepare_environment_preserves_operator_values_and_comments(tmp_path: Path) -> None:
    encoded = base64.urlsafe_b64encode(b"k" * 32).decode("ascii").rstrip("=")
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# operator comment must remain\n"
        "MYSQL_HOST=db.internal\n"
        "SECRET_KEY=operator-secret-value # keep this comment\n"
        "JWT_SECRET_KEY=your-jwt-secret-key-change-this-in-production # jwt comment\n"
        "TOKEN_HASH_KEY=\n"
        f'CREDENTIAL_ENCRYPTION_KEYS=\'{{"prod":"{encoded}"}}\' # keyring comment\n'
        'CREDENTIAL_ACTIVE_KEY_ID="prod" # active-key comment\n'
        "CUSTOM_OPERATOR_VALUE=unchanged\n",
        encoding="utf-8",
    )
    env_path.chmod(0o644)

    prepare_environment(env_path, PROJECT_ROOT / ".env.example")
    content = env_path.read_text(encoding="utf-8")
    values = dotenv_values(env_path)

    assert "# operator comment must remain" in content
    assert "SECRET_KEY=operator-secret-value # keep this comment" in content
    assert "# jwt comment" in content
    assert "# keyring comment" in content
    assert "# active-key comment" in content
    assert "CUSTOM_OPERATOR_VALUE=unchanged" in content
    assert values["MYSQL_HOST"] == "db.internal"
    assert values["SECRET_KEY"] == "operator-secret-value"
    assert values["JWT_SECRET_KEY"] != "your-jwt-secret-key-change-this-in-production"
    assert values["TOKEN_HASH_KEY"]
    assert values["CREDENTIAL_ENCRYPTION_KEYS"] == f'{{"prod":"{encoded}"}}'
    assert values["CREDENTIAL_ACTIVE_KEY_ID"] == "prod"
    assert values["REDIS_HOST"] == "localhost"
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_prepare_environment_hardens_unchanged_existing_file(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    prepare_environment(env_path, PROJECT_ROOT / ".env.example")
    original = env_path.read_text(encoding="utf-8")
    env_path.chmod(0o644)

    assert prepare_environment(env_path, PROJECT_ROOT / ".env.example") == [".env permissions"]
    assert env_path.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert prepare_environment(env_path, PROJECT_ROOT / ".env.example") == []


def test_prepare_environment_rejects_invalid_operator_keyring_without_rewriting(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    original = (
        "SECRET_KEY=operator-secret\n"
        "JWT_SECRET_KEY=operator-jwt\n"
        "TOKEN_HASH_KEY=operator-token-hash\n"
        'CREDENTIAL_ENCRYPTION_KEYS={"prod":"not-valid-base64!"}\n'
        "CREDENTIAL_ACTIVE_KEY_ID=prod\n"
    )
    env_path.write_text(original, encoding="utf-8")

    with pytest.raises(
        EnvironmentPreparationError,
        match="URL-safe base64",
    ):
        prepare_environment(env_path, PROJECT_ROOT / ".env.example")

    assert env_path.read_text(encoding="utf-8") == original


def _fake_upgrade_project(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    project = tmp_path / "upgrade project with spaces"
    (project / "scripts").mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "upgrade.sh", project / "upgrade.sh")
    shutil.copy2(PROJECT_ROOT / ".env.example", project / ".env.example")
    shutil.copy2(PROJECT_ROOT / "scripts/prepare_env.py", project / "scripts/prepare_env.py")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uv_log = tmp_path / "uv.log"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$UV_LOG"\n'
        'if [ "$1" = "sync" ]; then\n'
        '  if [ "${FAIL_SYNC:-0}" = "1" ]; then exit 6; fi\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" != "run" ]; then exit 64; fi\n'
        "shift\n"
        'if [ "$1" = "--locked" ]; then shift; fi\n'
        'if [ "$1" != "python" ]; then exit 65; fi\n'
        "shift\n"
        'if [ "$1" = "scripts/prepare_env.py" ]; then\n'
        '  exec "$REAL_PYTHON" "$@"\n'
        "fi\n"
        'if [ "$1" = "-m" ] && [ "$2" = "cs2_manager.migrate" ]; then\n'
        '  if [ "$3" = "upgrade" ] && [ "${FAIL_MIGRATION:-0}" = "1" ]; then exit 7; fi\n'
        '  if [ "$3" = "check" ] && [ "${FAIL_CHECK:-0}" = "1" ]; then exit 8; fi\n'
        "  exit 0\n"
        "fi\n"
        "exit 66\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "REAL_PYTHON": sys.executable,
        "UV_LOG": str(uv_log),
    }
    return project, uv_log, environment


def test_upgrade_script_works_from_any_cwd_without_starting_services(tmp_path: Path) -> None:
    project, uv_log, environment = _fake_upgrade_project(tmp_path)
    invocation_cwd = tmp_path / "elsewhere"
    invocation_cwd.mkdir()

    result = subprocess.run(
        ["bash", str(project / "upgrade.sh")],
        cwd=invocation_cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    commands = uv_log.read_text(encoding="utf-8").splitlines()
    assert commands[0] == "sync --python 3.14 --locked"
    assert commands[1].startswith("run --locked python scripts/prepare_env.py ")
    assert commands[2] == "run --locked python -m cs2_manager.migrate upgrade"
    assert commands[3] == "run --locked python -m cs2_manager.migrate check"
    assert all("uvicorn" not in command for command in commands)
    assert (project / ".env").is_file()


def test_upgrade_script_stops_before_check_when_migration_fails(tmp_path: Path) -> None:
    project, uv_log, environment = _fake_upgrade_project(tmp_path)
    environment["FAIL_MIGRATION"] = "1"

    result = subprocess.run(
        ["bash", str(project / "upgrade.sh")],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 7
    commands = uv_log.read_text(encoding="utf-8").splitlines()
    assert commands[-1] == "run --locked python -m cs2_manager.migrate upgrade"
    assert "run --locked python -m cs2_manager.migrate check" not in commands


def test_upgrade_script_stops_before_migration_when_env_is_invalid(tmp_path: Path) -> None:
    project, uv_log, environment = _fake_upgrade_project(tmp_path)
    original = (
        "SECRET_KEY=operator-secret\n"
        "JWT_SECRET_KEY=operator-jwt\n"
        "TOKEN_HASH_KEY=operator-token-hash\n"
        'CREDENTIAL_ENCRYPTION_KEYS={"prod":"invalid!"}\n'
        "CREDENTIAL_ACTIVE_KEY_ID=prod\n"
    )
    (project / ".env").write_text(original, encoding="utf-8")

    result = subprocess.run(
        ["bash", str(project / "upgrade.sh")],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    commands = uv_log.read_text(encoding="utf-8").splitlines()
    assert commands[-1].startswith("run --locked python scripts/prepare_env.py ")
    assert all("cs2_manager.migrate" not in command for command in commands)
    assert (project / ".env").read_text(encoding="utf-8") == original


def test_start_script_only_launches_from_its_project_directory(tmp_path: Path) -> None:
    project = tmp_path / "project with spaces"
    project.mkdir()
    shutil.copy2(PROJECT_ROOT / "start.sh", project / "start.sh")
    invocation_cwd = tmp_path / "elsewhere"
    invocation_cwd.mkdir()
    fake_bin = tmp_path / "start-bin"
    fake_bin.mkdir()
    uv_log = tmp_path / "start-uv.log"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        '#!/bin/sh\nprintf "%s|%s\\n" "$PWD" "$*" > "$UV_LOG"\n',
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "UV_LOG": str(uv_log),
    }

    result = subprocess.run(
        ["bash", str(project / "start.sh")],
        cwd=invocation_cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    working_directory, command = uv_log.read_text(encoding="utf-8").strip().split("|", 1)
    assert working_directory == str(project)
    assert command.startswith("run --python 3.14 --locked uvicorn main:app ")
    assert "prepare_env" not in command
    assert "cs2_manager.migrate" not in command


def test_deployment_shell_scripts_are_bash_syntax_valid() -> None:
    for script in ("upgrade.sh", "start.sh"):
        result = subprocess.run(
            ["bash", "-n", str(PROJECT_ROOT / script)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
