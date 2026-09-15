"""Evaluate marketplace index candidates against the isolated PostgreSQL.

Indexes are not submitted unless every inclusion gate passes. This module never
runs ``CREATE INDEX CONCURRENTLY``: startup migrations use an outer transaction,
and PostgreSQL forbids concurrent builds inside a transaction block.

Coverage is decided from index column lists, not names. EXPLAIN targets the
HTTP list shape (full rows, filters, sort, paging, and exact COUNT), not an
id-only microbenchmark.
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
        "columns": "framework,is_recommended,install_count,created_at,id",
    },
    {
        "name": "ix_market_plugins_framework_created_id",
        "purpose": "newest/oldest browse: framework + created_at + id",
        "ddl": (
            "CREATE INDEX ix_market_plugins_framework_created_id "
            "ON market_plugins (framework, created_at, id)"
        ),
        "columns": "framework,created_at,id",
    },
)

_LIST_COLUMNS = (
    "id, github_url, title, description, description_i18n, author, version, "
    "category, framework, tags, is_recommended, icon_url, dependencies, "
    "custom_install_path, download_count, ai_metadata, install_count, "
    "created_at, updated_at"
)

TARGET_QUERIES: tuple[dict[str, str], ...] = (
    {
        "name": "market_recommended",
        "sql": (
            f"SELECT {_LIST_COLUMNS} FROM market_plugins "
            "WHERE framework = 'COUNTERSTRIKESHARP' "
            "ORDER BY is_recommended DESC, install_count DESC, created_at DESC, id DESC "
            "LIMIT 20 OFFSET 0"
        ),
    },
    {
        "name": "market_newest",
        "sql": (
            f"SELECT {_LIST_COLUMNS} FROM market_plugins "
            "WHERE framework = 'COUNTERSTRIKESHARP' "
            "ORDER BY created_at DESC, id DESC LIMIT 20 OFFSET 0"
        ),
    },
    {
        "name": "market_oldest_page",
        "sql": (
            f"SELECT {_LIST_COLUMNS} FROM market_plugins "
            "WHERE framework = 'COUNTERSTRIKESHARP' "
            "ORDER BY created_at ASC, id ASC LIMIT 20 OFFSET 40"
        ),
    },
    {
        "name": "market_recommended_utility",
        "sql": (
            f"SELECT {_LIST_COLUMNS} FROM market_plugins "
            "WHERE framework = 'COUNTERSTRIKESHARP' AND category = 'UTILITY' "
            "ORDER BY is_recommended DESC, install_count DESC, created_at DESC, id DESC "
            "LIMIT 20 OFFSET 0"
        ),
    },
    {
        "name": "market_count",
        "sql": "SELECT count(*) FROM market_plugins WHERE framework = 'COUNTERSTRIKESHARP'",
    },
)


@dataclass(frozen=True, slots=True)
class IndexGateInput:
    existing_index_covers: bool
    p95_improvement: float | None
    write_within_tolerance: bool
    build_seconds: float | None


def parse_index_columns(indexdef: str) -> tuple[str, ...]:
    """Read btree columns from ``pg_indexes.indexdef``, ignoring sort options."""
    start = indexdef.rfind("(")
    end = indexdef.rfind(")")
    if start < 0 or end <= start:
        return ()
    columns: list[str] = []
    for part in indexdef[start + 1 : end].split(","):
        token = part.strip().strip('"').split()
        if not token:
            continue
        columns.append(token[0].lower())
    return tuple(columns)


def btree_covers(existing: tuple[str, ...], candidate: tuple[str, ...]) -> bool:
    """True when ``existing`` already has ``candidate`` as a left-to-right prefix."""
    if not candidate or len(existing) < len(candidate):
        return False
    wanted = tuple(item.lower() for item in candidate)
    return existing[: len(wanted)] == wanted


def candidate_columns(item: dict[str, str]) -> tuple[str, ...]:
    return tuple(part.strip().lower() for part in item["columns"].split(",") if part.strip())


def existing_covers_candidate(
    existing: list[dict[str, Any]],
    columns: tuple[str, ...],
) -> bool:
    return any(btree_covers(tuple(item.get("columns") or ()), columns) for item in existing)


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


def _candidate_report(
    item: dict[str, str],
    gate: IndexGateInput,
) -> dict[str, Any]:
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
    existing: list[dict[str, Any]],
    explains: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates = []
    for item in CANDIDATE_INDEXES:
        covered = existing_covers_candidate(existing, candidate_columns(item))
        gate = IndexGateInput(
            existing_index_covers=covered,
            p95_improvement=None,
            write_within_tolerance=False,
            build_seconds=None,
        )
        candidates.append(_candidate_report(item, gate))
    return {
        "skipped": False,
        "indexes_submitted": False,
        "existing_indexes": [item["name"] for item in existing],
        "existing_index_defs": existing,
        "existing_covers_candidates": all(
            item["gate"]["existing_index_covers"] for item in candidates
        ),
        "explains": explains,
        "candidates": candidates,
        "reason": (
            "EXPLAIN captured, but three-round HTTP p95, write tolerance, and "
            "index build time were not measured; candidates stay out"
        ),
    }


async def _explain_market_queries(
    connection: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from sqlalchemy import text

    explains: list[dict[str, Any]] = []
    for query in TARGET_QUERIES:
        result = await connection.execute(
            text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query['sql']}")
        )
        explains.append({"name": query["name"], "plan": result.scalar()})
    existing_rows = await connection.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE tablename = 'market_plugins' ORDER BY indexname"
        )
    )
    existing = [
        {
            "name": name,
            "definition": definition,
            "columns": list(parse_index_columns(definition)),
        }
        for name, definition in existing_rows.all()
    ]
    return explains, existing


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
            explains, existing = await _explain_market_queries(connection)
    except Exception as exc:
        return skipped_evaluation(str(exc))
    finally:
        if engine is not None:
            await engine.dispose()
    return _unmeasured_connected_report(existing, explains)
