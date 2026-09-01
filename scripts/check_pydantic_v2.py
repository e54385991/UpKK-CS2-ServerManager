#!/usr/bin/env python3
"""Reject Pydantic v1 imports and model APIs in maintained Python code."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEARCH_ROOTS = (PROJECT_ROOT / "api", PROJECT_ROOT / "modules", PROJECT_ROOT / "services")
DEPRECATED_IMPORTS = {"validator", "root_validator", "parse_obj_as", "BaseSettings"}
DEPRECATED_METHODS = {"parse_obj", "parse_raw", "from_orm"}


def _python_files():
    for root in SEARCH_ROOTS:
        yield from root.rglob("*.py")


def check() -> list[str]:
    violations: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "pydantic.v1":
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}: pydantic.v1"
                    )
                if node.module == "pydantic":
                    for alias in node.names:
                        if alias.name in DEPRECATED_IMPORTS:
                            violations.append(
                                f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}: {alias.name}"
                            )
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in DEPRECATED_METHODS:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}: .{node.func.attr}()"
                    )
            elif isinstance(node, ast.ClassDef):
                if any(
                    isinstance(item, ast.ClassDef) and item.name == "Config" for item in node.body
                ):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}: nested Config class"
                    )
    return violations


def main() -> int:
    violations = check()
    if violations:
        print("Pydantic v2 compatibility violations:")
        print("\n".join(f"  - {item}" for item in violations))
        return 1
    print("Pydantic v2 API check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
