"""Backend CI must supply its required configuration before importing the app."""

import re
import tomllib
from pathlib import Path

import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from modules.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_backend_ci_environment_can_initialize_settings_without_dotenv():
    workflow = yaml.safe_load((PROJECT_ROOT / ".github/workflows/quality.yml").read_text())
    environment = workflow["jobs"]["baseline"]["env"]
    required = {name for name, field in Settings.model_fields.items() if field.is_required()}
    assert required <= environment.keys(), (
        f"Missing required settings: {required - environment.keys()}"
    )

    Settings(_env_file=None, **environment)


def test_backend_runtime_configuration_targets_stable_python_315():
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pinned = (PROJECT_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    version = Version(pinned)
    assert version.release[:2] == (3, 15)
    assert not version.is_prerelease
    supported = SpecifierSet(project["project"]["requires-python"])
    assert version in supported
    assert "3.14.8" not in supported
    assert "3.15.0rc3" not in supported
    assert project["tool"]["ruff"]["target-version"] == "py315"
    assert project["tool"]["basedpyright"]["pythonVersion"] == "3.15"

    workflow = yaml.safe_load((PROJECT_ROOT / ".github/workflows/quality.yml").read_text())
    steps = workflow["jobs"]["baseline"]["steps"]
    setup = next(step for step in steps if step.get("uses", "").startswith("astral-sh/setup-uv@"))
    assert setup["with"]["python-version-file"] == ".python-version"
    for launcher in ("start.sh", "start.bat"):
        assert f"--python {pinned} --locked" in (PROJECT_ROOT / launcher).read_text()


def test_container_contracts_use_the_pinned_runtime_and_build_current_source():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    pin = re.search(r"^FROM python:([^\s@]+)@sha256:[a-f0-9]{64} AS python$", dockerfile, re.M)
    assert pin is not None
    version = Version(pin.group(1).split("-", 1)[0])
    assert version.release[:2] == (3, 15)
    assert version.pre is None or version.pre[0] == "rc"

    workflow = yaml.safe_load((PROJECT_ROOT / ".github/workflows/quality.yml").read_text())
    job = workflow["jobs"]["compose-health"]
    assert set(job["strategy"]["matrix"]["runner"]) == {"ubuntu-24.04", "ubuntu-24.04-arm"}
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "docker compose up -d --build --wait" in commands
    assert f'platform.python_version() == "{version}"' in commands
    assert 'sysconfig.get_config_var("Py_GIL_DISABLED")' in commands
    assert "--env POSTGRES_HOST=127.0.0.1" in commands
    assert "RUN_POSTGRES_INTEGRATION=1 uv run" in commands
