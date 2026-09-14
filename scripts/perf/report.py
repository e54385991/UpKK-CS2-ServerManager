"""Report schema, percentile reuse, and gate evaluation for isolated runs."""

from __future__ import annotations

import json
import math
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.perf.profiles import (
    CPU_RSS_GATE,
    IMPROVEMENT_GATE,
    LOCALE_BYTE_GATE,
    REGRESSION_FLOOR_MS,
    REGRESSION_RATIO,
)

FORMAT = "upkk-isolated-perf"
SCHEMA_VERSION = 1


def percentile_block(values: list[float]) -> dict[str, float]:
    """Same nearest-rank block as modules.observability.stats, without importing the app."""
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    ordered = sorted(values)
    last = ordered[-1]
    return {
        "p50": _from_ordered(ordered, 50),
        "p95": _from_ordered(ordered, 95),
        "p99": _from_ordered(ordered, 99),
        "max": last,
    }


def _from_ordered(ordered: list[float], percent: int) -> float:
    rank = max(1, math.ceil(percent / 100 * len(ordered)))
    return ordered[min(len(ordered), rank) - 1]


def git_sha(cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else "unknown"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def median_of_rounds(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def improvement_ratio(baseline: float, candidate: float) -> float:
    if baseline <= 0:
        return 0.0
    return (baseline - candidate) / baseline


def meets_improvement(baseline: float, candidate: float, gate: float = IMPROVEMENT_GATE) -> bool:
    return improvement_ratio(baseline, candidate) >= gate


def regression_limit_ms(baseline_p95: float) -> float:
    return baseline_p95 + max(baseline_p95 * REGRESSION_RATIO, REGRESSION_FLOOR_MS)


def within_regression(baseline_p95: float, candidate_p95: float) -> bool:
    return candidate_p95 <= regression_limit_ms(baseline_p95)


def within_resource_gate(baseline: float, candidate: float, gate: float = CPU_RSS_GATE) -> bool:
    if baseline <= 0:
        return candidate <= 0
    return candidate <= baseline * (1.0 + gate)


def soak_holds_rss(samples: Sequence[int], gate: float = CPU_RSS_GATE) -> bool:
    """Compare end RSS to the sample after the first load window, not t=0 warmup."""
    if len(samples) < 3:
        return False
    return within_resource_gate(float(samples[1]), float(samples[-1]), gate)


def locale_bytes_pass(before: int, after: int, gate: float = LOCALE_BYTE_GATE) -> bool:
    if before <= 0:
        return False
    return (before - after) / before >= gate


def empty_report(
    *,
    profile: str,
    mode: str,
    claimed_gains: bool = False,
    cwd: Path | None = None,
) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "version": SCHEMA_VERSION,
        "claimed_gains": claimed_gains,
        "captured_at": utc_now(),
        "git_sha": git_sha(cwd),
        "profile": profile,
        "mode": mode,
        "environment": {
            "postgres": "isolated-loopback",
            "redis": "isolated-loopback",
            "external": "stubbed",
        },
        "catalog_bytes": {},
        "browser": {"rounds": [], "summary": {}},
        "api": {"rounds": [], "summary": {}, "probes": {}},
        "backend": {"rounds": [], "summary": {}},
        "download": {"chunks": [], "prune_scans": None},
        "gates": {},
        "notes": [
            "Numbers in this report are measurements, not accepted production gains.",
            "Timing comparisons belong on a fixed isolated host, not default CI.",
        ],
    }


def attach_route_summary(samples_ms: list[float], *, errors: int, count: int) -> dict[str, Any]:
    ranked = percentile_block(samples_ms)
    return {
        "count": count,
        "errors": errors,
        "error_rate": (errors / count) if count else 0.0,
        "latency_ms": ranked,
        "throughput_rps": None,
    }


def compare_summaries(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate stated gates. Missing sections stay unverified."""
    gates: dict[str, Any] = {
        "inbox_p95_improvement": _ratio_gate(baseline, candidate, ("api", "summary", "inbox")),
        "locale_login_bytes": _locale_gate(baseline, candidate, "login"),
        "locale_overview_bytes": _locale_gate(baseline, candidate, "overview"),
        "p95_regression": _regression_gate(baseline, candidate),
    }
    return gates


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=_json_ready) + "\n",
        encoding="utf-8",
    )


def load_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("performance report must be an object")
    return payload


def _nested(payload: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _ratio_gate(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    path: tuple[str, ...],
) -> dict[str, Any]:
    before = _p95(_nested(baseline, path))
    after = _p95(_nested(candidate, path))
    if before is None or after is None:
        return {"verified": False, "reason": "missing p95"}
    ratio = improvement_ratio(before, after)
    return {
        "verified": True,
        "passed": meets_improvement(before, after),
        "baseline_p95_ms": before,
        "candidate_p95_ms": after,
        "improvement": ratio,
        "gate": IMPROVEMENT_GATE,
    }


def _locale_gate(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    route: str,
) -> dict[str, Any]:
    before = _int(_nested(baseline, ("catalog_bytes", "client_estimate", route)))
    after = _int(_nested(candidate, ("catalog_bytes", "client_estimate", route)))
    if before is None or after is None:
        return {"verified": False, "reason": "missing locale bytes"}
    return {
        "verified": True,
        "passed": locale_bytes_pass(before, after),
        "before_bytes": before,
        "after_bytes": after,
        "gate": LOCALE_BYTE_GATE,
    }


def _regression_gate(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    summary = _nested(baseline, ("api", "summary"))
    other = _nested(candidate, ("api", "summary"))
    if not isinstance(summary, Mapping) or not isinstance(other, Mapping):
        return {"verified": False, "reason": "missing api summary"}
    for name in summary:
        before = _p95(summary.get(name))
        after = _p95(other.get(name) if isinstance(other, Mapping) else None)
        if before is None or after is None:
            continue
        rows.append(
            {
                "route": name,
                "passed": within_regression(before, after),
                "baseline_p95_ms": before,
                "candidate_p95_ms": after,
                "limit_ms": regression_limit_ms(before),
            }
        )
    return {
        "verified": True,
        "passed": all(row["passed"] for row in rows) if rows else False,
        "routes": rows,
    }


def _p95(block: Any) -> float | None:
    if not isinstance(block, Mapping):
        return None
    latency = block.get("latency_ms", block)
    if not isinstance(latency, Mapping):
        return None
    value = latency.get("p95")
    return float(value) if isinstance(value, (int, float)) else None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None
