"""Regression checks for the 1Panel local application package."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_1panel_package_validator_passes() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_1panel_package.py"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
