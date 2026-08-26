#!/usr/bin/env python3
"""Enforce repository dependency direction and an acyclic service graph."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import grimp  # noqa: E402


def main() -> int:
    graph = grimp.build_graph("services", include_external_packages=False)
    cycle_breakers = sorted(graph.nominate_cycle_breakers("services"))
    if cycle_breakers:
        print("Service import cycles detected. Break at least these dependency edges:")
        for importer, imported in cycle_breakers:
            print(f"  - {importer} -> {imported}")
        return 1

    print(f"Service import graph is acyclic ({len(graph.modules)} modules checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
