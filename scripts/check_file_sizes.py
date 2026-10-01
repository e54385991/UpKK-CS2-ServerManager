#!/usr/bin/env python3
"""Keep production modules and tests small enough to own one responsibility."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (PROJECT_ROOT / "api", PROJECT_ROOT / "modules", PROJECT_ROOT / "services")
TEST_ROOT = PROJECT_ROOT / "tests"
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
PRODUCTION_LIMIT = 800
TEST_LIMIT = 1200
GENERATED_NAMES = {"schema.d.ts"}

LEGACY_SIZE_LIMITS: dict[str, int] = {}


def _python_files(root: Path):
    yield from root.rglob("*.py")


def _frontend_files(root: Path):
    for path in root.rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        if any(part in {"node_modules", ".next"} for part in path.parts):
            continue
        yield path


@dataclass(frozen=True)
class FileBudget:
    path: str
    lines: int
    limit: int


def file_budgets() -> Iterator[FileBudget]:
    for root in PRODUCTION_ROOTS:
        for path in _python_files(root):
            if path.name in GENERATED_NAMES or "alembic/versions" in path.as_posix():
                continue
            lines = len(path.read_text(encoding="utf-8").splitlines())
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            limit = LEGACY_SIZE_LIMITS.get(relative, PRODUCTION_LIMIT)
            yield FileBudget(relative, lines, limit)
    for path in _python_files(TEST_ROOT):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        limit = LEGACY_SIZE_LIMITS.get(relative, TEST_LIMIT)
        yield FileBudget(relative, lines, limit)
    for path in _frontend_files(FRONTEND_ROOT):
        if path.name in GENERATED_NAMES:
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        limit = LEGACY_SIZE_LIMITS.get(
            relative,
            TEST_LIMIT if "e2e" in path.parts or ".test." in path.name else PRODUCTION_LIMIT,
        )
        yield FileBudget(relative, lines, limit)


def _violations() -> list[str]:
    return [
        f"{item.path} has {item.lines} lines (limit {item.limit})"
        for item in file_budgets()
        if item.lines > item.limit
    ]


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
