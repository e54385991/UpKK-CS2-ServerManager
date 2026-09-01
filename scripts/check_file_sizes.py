#!/usr/bin/env python3
"""Keep production modules and tests small enough to own one responsibility."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (PROJECT_ROOT / "api", PROJECT_ROOT / "modules", PROJECT_ROOT / "services")
TEST_ROOT = PROJECT_ROOT / "tests"
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
PRODUCTION_LIMIT = 800
TEST_LIMIT = 1200
GENERATED_NAMES = {"schema.d.ts"}


def _python_files(root: Path):
    yield from root.rglob("*.py")


def _frontend_files(root: Path):
    for path in root.rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        if any(part in {"node_modules", ".next"} for part in path.parts):
            continue
        yield path


def _violations() -> list[str]:
    violations: list[str] = []
    for root in PRODUCTION_ROOTS:
        for path in _python_files(root):
            if path.name in GENERATED_NAMES or "alembic/versions" in path.as_posix():
                continue
            lines = len(path.read_text(encoding="utf-8").splitlines())
            if lines > PRODUCTION_LIMIT:
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)} has {lines} lines (limit {PRODUCTION_LIMIT})"
                )
    for path in _python_files(TEST_ROOT):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > TEST_LIMIT:
            violations.append(
                f"{path.relative_to(PROJECT_ROOT)} has {lines} lines (limit {TEST_LIMIT})"
            )
    for path in _frontend_files(FRONTEND_ROOT):
        if path.name in GENERATED_NAMES:
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        limit = TEST_LIMIT if "e2e" in path.parts or ".test." in path.name else PRODUCTION_LIMIT
        if lines > limit:
            violations.append(f"{path.relative_to(PROJECT_ROOT)} has {lines} lines (limit {limit})")
    return violations


def main() -> int:
    violations = _violations()
    if violations:
        print("File-size budget exceeded:")
        print("\n".join(f"  - {item}" for item in violations))
        return 1
    print("Production and test file-size budget passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
