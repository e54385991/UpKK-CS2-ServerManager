"""Segmented cost fields collected beside each isolated measurement."""

from __future__ import annotations

from typing import Any, Mapping

SEGMENT_KEYS = (
    "sql_count",
    "sql_ms",
    "db_checked_out",
    "redis_ops",
    "redis_ms",
    "redis_bytes",
    "lock_wait_ms",
    "snapshot_ms",
    "dto_ms",
    "json_ms",
    "loop_lag_ms",
)


def count_delta(before: Mapping[str, int], after: Mapping[str, int], name: str) -> int:
    return int(after.get(name, 0)) - int(before.get(name, 0))


def empty_segments() -> dict[str, Any]:
    return {key: None for key in SEGMENT_KEYS}


def attach_segments(
    *,
    sql_count: int | None = None,
    sql_ms: float | None = None,
    db_checked_out: int | None = None,
    redis_ops: int | None = None,
    redis_ms: float | None = None,
    redis_bytes: int | None = None,
    lock_wait_ms: float | None = None,
    snapshot_ms: float | None = None,
    dto_ms: float | None = None,
    json_ms: float | None = None,
    loop_lag_ms: float | None = None,
) -> dict[str, Any]:
    """Fill known counters; leave later hub timings None until they exist."""
    return {
        "sql_count": sql_count,
        "sql_ms": sql_ms,
        "db_checked_out": db_checked_out,
        "redis_ops": redis_ops,
        "redis_ms": redis_ms,
        "redis_bytes": redis_bytes,
        "lock_wait_ms": lock_wait_ms,
        "snapshot_ms": snapshot_ms,
        "dto_ms": dto_ms,
        "json_ms": json_ms,
        "loop_lag_ms": loop_lag_ms,
    }


def segments_from_counts(
    before: Mapping[str, int],
    after: Mapping[str, int],
    *,
    sql_ms: float | None = None,
    redis_ms: float | None = None,
    redis_bytes: int | None = None,
    db_checked_out: int | None = None,
    loop_lag_ms: float | None = None,
    lock_wait_ms: float | None = None,
    snapshot_ms: float | None = None,
    dto_ms: float | None = None,
    json_ms: float | None = None,
) -> dict[str, Any]:
    return attach_segments(
        sql_count=count_delta(before, after, "db.executions"),
        sql_ms=sql_ms,
        db_checked_out=db_checked_out,
        redis_ops=count_delta(before, after, "redis.ops"),
        redis_ms=redis_ms,
        redis_bytes=redis_bytes,
        lock_wait_ms=lock_wait_ms,
        snapshot_ms=snapshot_ms,
        dto_ms=dto_ms,
        json_ms=json_ms,
        loop_lag_ms=loop_lag_ms,
    )


def latest_sample(samples: Mapping[str, list[float]], name: str) -> float | None:
    values = samples.get(name) or []
    if not values:
        return None
    return float(values[-1])


def panel_checked_out(panel: Mapping[str, Any] | None) -> int | None:
    if not isinstance(panel, Mapping):
        return None
    database = panel.get("database")
    if not isinstance(database, Mapping):
        return None
    value = database.get("checked_out")
    return int(value) if isinstance(value, int) else None
