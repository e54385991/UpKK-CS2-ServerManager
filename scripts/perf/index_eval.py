"""Evaluate marketplace index candidates against the isolated PostgreSQL.

Indexes are not submitted unless every inclusion gate passes. This module never
runs ``CREATE INDEX CONCURRENTLY``: startup migrations use an outer transaction,
and PostgreSQL forbids concurrent builds inside a transaction block.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

INDEX_BUILD_LIMIT_SECONDS = 5.0
P95_IMPROVEMENT_REQUIRED = 0.20

CANDIDATE_INDEXES: tuple[dict[str, str], ...] = (
    {
        "name": "ix_market_plugins_framework_recommended_installs_created",
        "purpose": (
            "recommended browse: framework + is_recommended + install_count + created_at + id"
        ),
        "ddl": (
            "CREATE INDEX ix_market_plugins_framework_recommended_installs_created "
            "ON market_plugins (framework, is_recommended, install_count, created_at, id)"
        ),
    },
    {
        "name": "ix_market_plugins_framework_created_id",
        "purpose": "newest/oldest browse: framework + created_at + id",
        "ddl": (
            "CREATE INDEX ix_market_plugins_framework_created_id "
            "ON market_plugins (framework, created_at, id)"
        ),
    },
)

TARGET_QUERIES: tuple[dict[str, str], ...] = (
    {
        "name": "market_recommended",
        "sql": (
            "SELECT id FROM market_plugins WHERE framework = 'counterstrikesharp' "
            "ORDER BY is_recommended DESC, install_count DESC, created_at DESC, id DESC "
            "LIMIT 20 OFFSET 0"
        ),
    },
    {
        "name": "market_newest",
        "sql": (
            "SELECT id FROM market_plugins WHERE framework = 'counterstrikesharp' "
            "ORDER BY created_at DESC, id DESC LIMIT 20 OFFSET 0"
        ),
    },
    {
        "name": "market_count",
        "sql": "SELECT count(*) FROM market_plugins WHERE framework = 'counterstrikesharp'",
    },
)


@dataclass(frozen=True, slots=True)
class IndexGateInput:
    existing_index_covers: bool
    p95_improvement: float | None
    write_within_tolerance: bool
    build_seconds: float | None


def index_inclusion_passes(candidate: IndexGateInput) -> bool:
    """All four inclusion rules must hold. Missing measurements fail closed."""
    if candidate.existing_index_covers:
        return False
    if candidate.p95_improvement is None:
        return False
    if candidate.p95_improvement < P95_IMPROVEMENT_REQUIRED:
        return False
    if not candidate.write_within_tolerance:
        return False
    if candidate.build_seconds is None:
        return False
    return candidate.build_seconds <= INDEX_BUILD_LIMIT_SECONDS


def _candidate_report(item: dict[str, str], gate: IndexGateInput) -> dict[str, Any]:
    return {
        **item,
        "included": index_inclusion_passes(gate),
        "gate": asdict(gate),
    }


def skipped_evaluation(reason: str) -> dict[str, Any]:
    unmeasured = IndexGateInput(
        existing_index_covers=False,
        p95_improvement=None,
        write_within_tolerance=False,
        build_seconds=None,
    )
    return {
        "skipped": True,
        "reason": reason,
        "indexes_submitted": False,
        "candidates": [_candidate_report(item, unmeasured) for item in CANDIDATE_INDEXES],
        "queries": list(TARGET_QUERIES),
    }


def _unmeasured_connected_report(
    existing_names: list[str], explains: list[dict[str, Any]]
) -> dict[str, Any]:
    candidate_names = {item["name"] for item in CANDIDATE_INDEXES}
    covers = candidate_names.issubset(set(existing_names))
    gate = IndexGateInput(
        existing_index_covers=covers,
        p95_improvement=None,
        write_within_tolerance=False,
        build_seconds=None,
    )
    return {
        "skipped": False,
        "indexes_submitted": False,
        "existing_indexes": existing_names,
        "existing_covers_candidates": covers,
        "explains": explains,
        "candidates": [_candidate_report(item, gate) for item in CANDIDATE_INDEXES],
        "reason": (
            "EXPLAIN captured, but three-round p95, write tolerance, and "
            "index build time were not measured; candidates stay out"
        ),
    }


async def _explain_market_queries(connection: Any) -> tuple[list[dict[str, Any]], list[str]]:
    from sqlalchemy import text

    explains: list[dict[str, Any]] = []
    for query in TARGET_QUERIES:
        result = await connection.execute(
            text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query['sql']}")
        )
        explains.append({"name": query["name"], "plan": result.scalar()})
    existing = await connection.execute(
        text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'market_plugins' ORDER BY indexname"
        )
    )
    return explains, [row[0] for row in existing.all()]


async def evaluate_market_indexes() -> dict[str, Any]:
    """Connect to the isolated database and EXPLAIN the marketplace queries.

    When PostgreSQL is unreachable, return a skipped report. Never create an
    Alembic revision from a skipped or failed-gate result.
    """
    try:
        from sqlalchemy.ext.asyncio import create_async_engine

        from modules.config import get_settings
        from scripts.perf.env import assert_isolated_target
    except Exception as exc:  # pragma: no cover - import surface only
        return skipped_evaluation(f"imports unavailable: {exc}")

    settings = get_settings()
    assert_isolated_target(settings)
    engine = None
    try:
        engine = create_async_engine(settings.database_url, pool_size=1)
        async with engine.connect() as connection:
            explains, existing_names = await _explain_market_queries(connection)
    except Exception as exc:
        return skipped_evaluation(str(exc))
    finally:
        if engine is not None:
            await engine.dispose()
    return _unmeasured_connected_report(existing_names, explains)
