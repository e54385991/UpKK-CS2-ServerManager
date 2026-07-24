#!/usr/bin/env python3
"""Run the same repository quality baseline used by CI."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENV_BIN = Path(sys.executable).parent
COVERAGE_BASELINE = int((PROJECT_ROOT / ".github" / "coverage-baseline.txt").read_text().strip())


def executable(name: str) -> str:
    local = VENV_BIN / name
    if local.is_file():
        return str(local)
    resolved = shutil.which(name)
    if resolved is None:
        raise SystemExit(f"Required command is not installed: {name}")
    return resolved


def run(label: str, command: list[str]) -> None:
    print(f"\n==> {label}", flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def main() -> None:
    uv = executable("uv")
    ruff = executable("ruff")
    pytest = executable("pytest")
    pip_audit = executable("pip-audit")
    npm = executable("npm")
    basedpyright = executable("basedpyright")
    lint_imports = executable("lint-imports")

    checks = (
        ("Lock file", [uv, "lock", "--check"]),
        ("Ruff format", [ruff, "format", "--check", "."]),
        ("Ruff lint", [ruff, "check", "."]),
        ("Progressive type checking", [basedpyright]),
        ("Import architecture", [lint_imports]),
        (
            "Tests, branch coverage, and compatibility contracts",
            [
                pytest,
                "-q",
                "--cov",
                "--cov-branch",
                "--cov-report=term",
                "--cov-report=xml:coverage.xml",
                f"--cov-fail-under={COVERAGE_BASELINE}",
            ],
        ),
        (
            "Templates and vendored static files",
            [sys.executable, "scripts/validate_console_templates.py"],
        ),
        ("Python dependency audit", [pip_audit, "-r", "requirements.txt"]),
        ("Frontend dependency audit", [npm, "audit", "--omit=dev"]),
    )
    for label, command in checks:
        run(label, command)

    print("\nAll baseline checks passed.")


if __name__ == "__main__":
    main()
