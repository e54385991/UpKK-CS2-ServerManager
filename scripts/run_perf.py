#!/usr/bin/env python3
"""Apply isolated environment variables, then run the performance harness."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.perf.env import apply_isolated_env, isolated_environ, ports_from_environ  # noqa: E402

apply_isolated_env(isolated_environ(ports_from_environ()))

from scripts.perf.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
