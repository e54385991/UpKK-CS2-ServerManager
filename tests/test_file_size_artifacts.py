from pathlib import Path

from scripts.check_file_sizes import FRONTEND_ARTIFACT_DIRS, _frontend_files


def test_frontend_budget_checks_source_and_tests_without_scanning_generated_builds(tmp_path: Path):
    for name in FRONTEND_ARTIFACT_DIRS:
        directory = tmp_path / name / "nested"
        directory.mkdir(parents=True)
        (directory / "validator.ts").write_text("generated\n" * 1201)
    for name in ("src/page.tsx", "e2e/navigation.spec.ts", "next.config.ts"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("source\n")
    assert {path.relative_to(tmp_path).as_posix() for path in _frontend_files(tmp_path)} == {
        "src/page.tsx",
        "e2e/navigation.spec.ts",
        "next.config.ts",
    }
