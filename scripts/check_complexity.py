#!/usr/bin/env python3
"""Enforce the repository-wide function complexity budget."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("api", "modules", "services", "scripts")


def main() -> int:
    ruff = shutil.which("ruff")
    if ruff is None:
        ruff = str(Path(sys.executable).parent / "ruff")
    command = [
        ruff,
        "check",
        *TARGETS,
        "--select",
        "C901",
        "--config",
        "lint.mccabe.max-complexity=15",
        "--output-format",
        "concise",
    ]
    result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if result.returncode:
        print("Complexity budget exceeded: all functions must stay <= 15")
        return result.returncode
    print("Repository complexity budget passed (max 15).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
