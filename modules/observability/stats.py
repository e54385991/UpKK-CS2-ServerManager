"""Nearest-rank percentiles for monitoring samples."""

from __future__ import annotations

import math


def nearest_rank(values: list[float], percent: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(percent / 100 * len(ordered)))
    return ordered[min(len(ordered), rank) - 1]


def percentile_block(values: list[float]) -> dict[str, float]:
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
