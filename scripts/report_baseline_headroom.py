#!/usr/bin/env python3
"""Report approaching quality budgets without adding a CI failure gate."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

if __package__:
    from .check_complexity import MAX_COMPLEXITY, TARGETS
    from .check_file_sizes import PROJECT_ROOT, file_budgets
else:
    from check_complexity import MAX_COMPLEXITY, TARGETS
    from check_file_sizes import PROJECT_ROOT, file_budgets

FILE_WARNING_RATIO = 0.9
COMPLEXITY_WARNING = 13
BUNDLE_WARNING_RATIO = 0.95


def _complexity_diagnostics(*, ignore_noqa: bool):
    ruff = shutil.which("ruff") or str(Path(sys.executable).parent / "ruff")
    command = [
        ruff,
        "check",
        *TARGETS,
        "--select",
        "C901",
        "--config",
        f"lint.mccabe.max-complexity={COMPLEXITY_WARNING - 1}",
        "--output-format=json",
    ]
    if ignore_noqa:
        command.append("--ignore-noqa")
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or "Ruff measurement failed")
    diagnostics = json.loads(result.stdout)
    if not isinstance(diagnostics, list):
        raise ValueError("Ruff did not return a diagnostic list")
    return diagnostics


def complexity_budgets() -> list[dict[str, object]]:
    diagnostics = _complexity_diagnostics(ignore_noqa=True)
    visible = {
        (item["filename"], item["location"]["row"])
        for item in _complexity_diagnostics(ignore_noqa=False)
    }
    items = []
    for diagnostic in diagnostics:
        match = re.search(r"\((\d+) >", diagnostic["message"])
        if match is None:
            raise ValueError("Unrecognized Ruff complexity diagnostic")
        path = Path(diagnostic["filename"])
        line = diagnostic["location"]["row"]
        value = int(match.group(1))
        items.append(
            {
                "path": path.relative_to(PROJECT_ROOT).as_posix(),
                "line": line,
                "function": diagnostic["message"].split("`", 2)[1],
                "value": value,
                "limit": MAX_COMPLEXITY,
                "remaining": MAX_COMPLEXITY - value,
                "suppressed": (diagnostic["filename"], line) not in visible,
            }
        )
    return items


def bundle_budgets() -> list[dict[str, object]]:
    # Read the production manifests through the same measurer used by the gate.
    # This deliberately does not build, start Next or contact a backend.
    source = """
import { measureBundles, INITIAL_ROUTE_BUDGET, INITIAL_CHUNK_BUDGET }
  from './frontend/scripts/bundle-budget.mjs';
const result = await measureBundles('frontend/.next');
const rows = [
  ...result.routes.map(({route, gzip}) => ({kind: 'route', path: route, value: gzip, limit: INITIAL_ROUTE_BUDGET})),
  ...[...result.chunkSizes].map(([path, value]) => ({kind: 'chunk', path, value, limit: INITIAL_CHUNK_BUDGET})),
];
console.log(JSON.stringify(rows));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Bundle measurement failed")
    rows = json.loads(result.stdout)
    return [
        {**row, "remaining": row["limit"] - row["value"]}
        for row in rows
        if row["value"] >= row["limit"] * BUNDLE_WARNING_RATIO
    ]


def collect_report(*, include_bundles: bool = False) -> dict[str, object]:
    report: dict[str, object] = {"files": [], "complexity": [], "bundles": [], "unavailable": []}
    unavailable: list[str] = []
    collectors = {
        "files": lambda: [
            {**asdict(item), "remaining": item.limit - item.lines}
            for item in file_budgets()
            if item.lines >= item.limit * FILE_WARNING_RATIO
        ],
        "complexity": complexity_budgets,
    }
    if include_bundles:
        collectors["bundles"] = bundle_budgets
    for name, collector in collectors.items():
        try:
            report[name] = collector()
        except Exception as error:
            unavailable.append(f"{name}: {error}")
    report["unavailable"] = unavailable
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--include-bundles", action="store_true")
    args = parser.parse_args()
    report = collect_report(include_bundles=args.include_bundles)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print("Advisory baseline headroom (does not change hard gates):")
    for name in ("files", "complexity", "bundles", "unavailable"):
        print(f"  {name}:")
        for item in report[name]:
            print(f"    {json.dumps(item, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
