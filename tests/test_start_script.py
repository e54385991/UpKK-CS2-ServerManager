"""Launcher contract for repo-root start.sh."""

from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_SH = PROJECT_ROOT / "start.sh"


def test_start_sh_is_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(START_SH)], check=True)


def test_start_sh_help_lists_build_and_start_options() -> None:
    result = subprocess.run(
        ["bash", str(START_SH), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    output = result.stdout
    assert "build+start" in output
    assert "./start.sh build" in output
    assert "./start.sh start" in output
    assert "./start.sh api" in output
    assert "./start.sh dev" in output
    assert "localhost:31800" in output
    assert ":3000" not in output
