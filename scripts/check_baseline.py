#!/usr/bin/env python3
"""Run the same repository quality baseline used by CI."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENV_BIN = Path(sys.executable).parent


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
    pre_commit = executable("pre-commit")
    ruff = executable("ruff")
    pytest = executable("pytest")
    pip_audit = executable("pip-audit")
    lint_imports = executable("lint-imports")
    npm = executable("npm")

    checks = (
        ("Lock file", [uv, "lock", "--check"]),
        ("Pre-commit hooks", [pre_commit, "run", "--all-files", "--show-diff-on-failure"]),
        ("Ruff format", [ruff, "format", "--check", "."]),
        ("Ruff lint", [ruff, "check", "."]),
        ("Layer dependency contracts", [lint_imports, "--no-cache"]),
        (
            "Acyclic service imports",
            [sys.executable, "scripts/check_architecture.py"],
        ),
        (
            "Complexity budget for split domains",
            [sys.executable, "scripts/check_complexity.py"],
        ),
        ("Tests and compatibility contracts", [pytest, "-q"]),
        (
            "Coverage for newly split domains",
            [
                pytest,
                "-q",
                "tests/test_ai_streaming_unit.py",
                "tests/test_ai_streaming.py",
                "tests/test_ai_assistant_security.py",
                "tests/test_ai_domain_units.py",
                "tests/test_discord_bot_agent_policy.py",
                "tests/test_batch_performance_contracts.py",
                "--cov=services.ai",
                "--cov=services.discord",
                "--cov=services.servers",
                "--cov-fail-under=90",
                "--cov-report=term-missing",
            ],
        ),
        (
            "Templates and vendored static files",
            [sys.executable, "scripts/validate_console_templates.py"],
        ),
        ("Frontend module tests", [npm, "run", "test:frontend"]),
        ("Frontend syntax", [npm, "run", "check:frontend"]),
        ("Python dependency audit", [pip_audit, "-r", "requirements.txt"]),
        ("Frontend dependency audit", [npm, "audit", "--omit=dev"]),
    )
    for label, command in checks:
        run(label, command)

    print("\nAll baseline checks passed.")


if __name__ == "__main__":
    main()
