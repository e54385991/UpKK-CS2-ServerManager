"""Advisory thresholds and unavailable collectors must not replace hard gates."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import report_baseline_headroom as report
from scripts.check_file_sizes import FileBudget


def test_file_warnings_include_boundary_and_reuse_each_budget(monkeypatch):
    monkeypatch.setattr(
        report,
        "file_budgets",
        lambda: iter(
            [
                FileBudget("api/below.py", 719, 800),
                FileBudget("api/boundary.py", 720, 800),
                FileBudget("tests/boundary.py", 1080, 1200),
            ]
        ),
    )
    monkeypatch.setattr(report, "complexity_budgets", lambda: [])
    result = report.collect_report()
    assert result["files"] == [
        {"path": "api/boundary.py", "lines": 720, "limit": 800, "remaining": 80},
        {"path": "tests/boundary.py", "lines": 1080, "limit": 1200, "remaining": 120},
    ]
    assert result["unavailable"] == []


def test_complexity_includes_suppressed_functions_without_mislabeling_other_noqa(monkeypatch):
    root = Path("/test-repository")

    def diagnostic(name, row, value):
        return {
            "filename": str(root / "services/example.py"),
            "location": {"row": row},
            "message": f"`{name}` is too complex ({value} > 12)",
        }

    visible = diagnostic("visible", 10, 13)
    suppressed = diagnostic("suppressed", 20, 19)
    monkeypatch.setattr(report, "PROJECT_ROOT", root)
    monkeypatch.setattr(
        report,
        "_complexity_diagnostics",
        lambda *, ignore_noqa: [visible, suppressed] if ignore_noqa else [visible],
    )
    assert report.complexity_budgets() == [
        {
            "path": "services/example.py",
            "line": 10,
            "function": "visible",
            "value": 13,
            "limit": 15,
            "remaining": 2,
            "suppressed": False,
        },
        {
            "path": "services/example.py",
            "line": 20,
            "function": "suppressed",
            "value": 19,
            "limit": 15,
            "remaining": -4,
            "suppressed": True,
        },
    ]


@pytest.mark.parametrize("failure", [OSError, TypeError, IndexError])
def test_collector_failure_is_advisory_and_other_results_survive(monkeypatch, failure):
    def unavailable():
        raise failure("ruff unavailable")

    monkeypatch.setattr(report, "file_budgets", lambda: iter([]))
    monkeypatch.setattr(report, "complexity_budgets", unavailable)
    monkeypatch.setattr(report, "bundle_budgets", lambda: [{"path": "page", "value": 100}])
    result = report.collect_report(include_bundles=True)
    assert result["complexity"] == []
    assert result["bundles"] == [{"path": "page", "value": 100}]
    assert result["unavailable"] == ["complexity: ruff unavailable"]


def test_bundle_collector_reads_existing_artifacts_and_includes_boundary(monkeypatch):
    commands = []

    def execute(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout='[{"path":"below","value":94,"limit":100},{"path":"boundary","value":95,"limit":100}]',
            stderr="",
        )

    monkeypatch.setattr(report.subprocess, "run", execute)
    assert report.bundle_budgets() == [
        {"path": "boundary", "value": 95, "limit": 100, "remaining": 5}
    ]
    assert len(commands) == 1
    assert commands[0][:2] == ["node", "--input-type=module"]
    assert "measureBundles('frontend/.next')" in commands[0][-1]
    assert "next build" not in commands[0][-1]
