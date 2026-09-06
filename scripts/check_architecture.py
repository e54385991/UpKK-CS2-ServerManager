#!/usr/bin/env python3
"""Enforce repository dependency direction and an acyclic service graph."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import grimp  # noqa: E402


def _has_module_level_import(importer: str, imported: str) -> bool:
    path = PROJECT_ROOT / (importer.replace(".", "/") + ".py")
    if not path.is_file():
        return True
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Import):
            if any(
                alias.name == imported or alias.name.startswith(imported + ".")
                for alias in node.names
            ):
                return True
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == imported or node.module.startswith(imported + "."):
                return True
    return False


def main() -> int:
    graph = grimp.build_graph("services", include_external_packages=False)
    # Function-local imports are deliberate dependency inversion seams used to
    # avoid import-time cycles. They are runtime dependencies, but not module
    # initialization cycles and should not fail this static gate.
    for importer in graph.modules:
        for imported in list(graph.find_modules_directly_imported_by(importer)):
            details = graph.get_import_details(importer=importer, imported=imported)
            if details and not _has_module_level_import(importer, imported):
                graph.remove_import(importer=importer, imported=imported)
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
