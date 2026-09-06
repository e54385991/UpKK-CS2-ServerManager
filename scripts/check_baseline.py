#!/usr/bin/env python3
"""Run the same repository quality baseline used by CI."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from time import monotonic

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENV_BIN = Path(sys.executable).parent

# The legacy full-source suite established this fixed ratchet floor. Keep it
# while those modules are split and tested in smaller domains; a newer local
# coverage measurement is evidence, not a reason to weaken this gate.
FULL_PYTHON_COVERAGE_FLOOR = "86.70"


def executable(name: str) -> str:
    local = VENV_BIN / name
    if local.is_file():
        return str(local)
    resolved = shutil.which(name)
    if resolved is None:
        raise SystemExit(f"Required command is not installed: {name}")
    return resolved


def run(label: str, command: list[str]) -> bool:
    print(f"\n==> {label}", flush=True)
    started = monotonic()
    result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    print(f"Completed: {label} ({monotonic() - started:.2f}s)", flush=True)
    if result.returncode:
        print(f"FAILED: {label} (exit code {result.returncode})", flush=True)
        return False
    return True


def main() -> None:
    uv = executable("uv")
    pre_commit = executable("pre-commit")
    ruff = executable("ruff")
    basedpyright = executable("basedpyright")
    pytest = executable("pytest")
    pip_audit = executable("pip-audit")
    lint_imports = executable("lint-imports")
    npm = executable("npm")

    checks = (
        ("Lock file", [uv, "lock", "--check"]),
        ("Pre-commit hooks", [pre_commit, "run", "--all-files", "--show-diff-on-failure"]),
        ("Ruff format", [ruff, "format", "--check", "."]),
        ("Ruff lint", [ruff, "check", "."]),
        ("BasedPyright type checking", [basedpyright, "--warnings"]),
        ("Layer dependency contracts", [lint_imports, "--no-cache"]),
        (
            "Acyclic service imports",
            [sys.executable, "scripts/check_architecture.py"],
        ),
        (
            "Repository complexity budget",
            [sys.executable, "scripts/check_complexity.py"],
        ),
        (
            "HTTP contract and response safety",
            [sys.executable, "scripts/check_api_contracts.py"],
        ),
        (
            "Pydantic v2 API compatibility",
            [sys.executable, "scripts/check_pydantic_v2.py"],
        ),
        (
            "Production and test file-size budget",
            [sys.executable, "scripts/check_file_sizes.py"],
        ),
        (
            "1Panel application package",
            [sys.executable, "scripts/check_1panel_package.py"],
        ),
        (
            "Tests, compatibility contracts and full Python coverage",
            [
                pytest,
                "-q",
                "--cov=api",
                "--cov=modules",
                "--cov=services",
                "--cov-branch",
                f"--cov-fail-under={FULL_PYTHON_COVERAGE_FLOOR}",
                "--cov-report=term-missing",
            ],
        ),
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
                "tests/test_telemetry_batches.py",
                "--cov=services.ai",
                "--cov=services.discord",
                "--cov=services.servers",
                "--cov-branch",
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
        (
            "Next.js module tests",
            [npm, "--prefix", str(PROJECT_ROOT / "frontend"), "run", "test:unit"],
        ),
        (
            "Next.js lint",
            [npm, "--prefix", str(PROJECT_ROOT / "frontend"), "run", "lint"],
        ),
        (
            "Next.js typecheck",
            [npm, "--prefix", str(PROJECT_ROOT / "frontend"), "run", "typecheck"],
        ),
        (
            "Next.js production build",
            [npm, "--prefix", str(PROJECT_ROOT / "frontend"), "run", "build"],
        ),
        (
            "Next.js bundle budget",
            [npm, "--prefix", str(PROJECT_ROOT / "frontend"), "run", "check:bundle"],
        ),
        ("Python dependency audit", [pip_audit, "-r", "requirements.txt"]),
        ("Legacy frontend dependency audit", [npm, "audit", "--omit=dev"]),
        (
            "Next.js dependency audit",
            [npm, "--prefix", str(PROJECT_ROOT / "frontend"), "audit", "--omit=dev"],
        ),
    )
    failures: list[str] = []
    for label, command in checks:
        if not run(label, command):
            failures.append(label)

    if failures:
        print("\nBaseline checks failed:")
        for label in failures:
            print(f"  - {label}")
        raise SystemExit(1)
    print("\nAll baseline checks passed.")


if __name__ == "__main__":
    main()
