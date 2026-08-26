#!/usr/bin/env python3
"""Enforce the complexity budget for newly split domains and workflows.

The repository still contains historical workflow functions that are being
retired incrementally.  The gate deliberately covers every new domain module
and the batch route now, so new code cannot add to that debt.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    "services/ai",
    "services/discord",
    "services/servers",
    "api/routes/actions/batch.py",
)


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
        print("Complexity budget exceeded: new domain/workflow functions must stay <= 15")
        return result.returncode
    print("Complexity budget passed for newly split domains (max 15).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
