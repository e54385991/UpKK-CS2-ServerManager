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

# These modules predate the 800-line ownership budget and are being split in
# follow-up domain refactors. Keep their current size as a ratchet so the
# baseline can be green without allowing further growth while preserving the
# stricter default for every new module.
LEGACY_SIZE_LIMITS = {
    "tests/test_ai_agent_enhancements.py": 2082,
    "tests/test_file_manager_archive.py": 1371,
    "tests/test_discord_bot_agent_policy.py": 1867,
}


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
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            limit = LEGACY_SIZE_LIMITS.get(relative, PRODUCTION_LIMIT)
            if lines > limit:
                violations.append(f"{relative} has {lines} lines (limit {limit})")
    for path in _python_files(TEST_ROOT):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        limit = LEGACY_SIZE_LIMITS.get(relative, TEST_LIMIT)
        if lines > limit:
            violations.append(f"{relative} has {lines} lines (limit {limit})")
    for path in _frontend_files(FRONTEND_ROOT):
        if path.name in GENERATED_NAMES:
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        limit = LEGACY_SIZE_LIMITS.get(
            relative,
            TEST_LIMIT if "e2e" in path.parts or ".test." in path.name else PRODUCTION_LIMIT,
        )
        if lines > limit:
            violations.append(f"{relative} has {lines} lines (limit {limit})")
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
