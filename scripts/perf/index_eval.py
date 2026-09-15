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
from typing import Any, Mapping

from scripts.perf.report import improvement_ratio, percentile_block, within_regression

INDEX_BUILD_LIMIT_SECONDS = 5.0
P95_IMPROVEMENT_REQUIRED = 0.20
WRITE_PROBE_COUNT = 30
WRITE_URL_PREFIX = "https://perf-index.invalid/write"
WRITE_INSERT_SQL = (
    "INSERT INTO market_plugins "
    "(github_url, title, category, framework, is_recommended, "
    "install_count, download_count) "
    "VALUES (:url, :title, 'UTILITY', 'COUNTERSTRIKESHARP', false, 0, 0)"
)

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


def write_p95_within_tolerance(before_p95: float, after_p95: float) -> bool:
    """Writes stay inside the same ``max(5%, 20 ms)`` envelope as other routes."""
    return within_regression(before_p95, after_p95)


def plan_execution_ms(plan: Any) -> float | None:
    root: Any = plan[0] if isinstance(plan, list) and plan else plan
    if not isinstance(root, dict):
        return None
    node = root.get("Plan", root)
    if not isinstance(node, dict):
        return None
    value = node.get("Actual Total Time")
    return None if value is None else float(value)


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


def attach_http_p95(
    evaluation: dict[str, Any],
    improvement: float | None,
) -> dict[str, Any]:
    """Recompute inclusion after a three-round HTTP market series."""
    rebuilt = [
        _candidate_report(
            {
                "name": item["name"],
                "purpose": item["purpose"],
                "ddl": item["ddl"],
                "columns": item["columns"],
            },
            IndexGateInput(
                existing_index_covers=bool(item["gate"]["existing_index_covers"]),
                p95_improvement=improvement,
                write_within_tolerance=bool(item["gate"]["write_within_tolerance"]),
                build_seconds=item["gate"].get("build_seconds"),
            ),
        )
        for item in evaluation.get("candidates", ())
    ]
    updated = dict(evaluation)
    updated["candidates"] = rebuilt
    updated["http_p95_improvement"] = improvement
    updated["indexes_submitted"] = bool(rebuilt) and all(row["included"] for row in rebuilt)
    if updated["indexes_submitted"]:
        updated["reason"] = "all inclusion gates passed"
    elif improvement is None:
        updated["reason"] = evaluation.get(
            "reason",
            "three-round HTTP market p95 is still missing; candidates stay out",
        )
    else:
        updated["reason"] = (
            "HTTP three-round market p95 improvement is below 20% or another "
            "inclusion gate failed; candidates stay out"
        )
    return updated


def market_p95_from_report(report: Mapping[str, Any]) -> float | None:
    """Read the three-round market median p95 from an upkk-isolated-perf file."""
    api = report.get("api")
    if not isinstance(api, Mapping):
        return None
    summary = api.get("summary")
    if not isinstance(summary, Mapping):
        return None
    market = summary.get("market")
    if not isinstance(market, Mapping):
        return None
    latency = market.get("latency_ms")
    if isinstance(latency, Mapping) and latency.get("p95") is not None:
        return float(latency["p95"])
    return None


def attach_http_from_reports(
    evaluation: dict[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach HTTP improvement from a without-index / with-index pair."""
    before_p95 = market_p95_from_report(before)
    after_p95 = market_p95_from_report(after)
    if before_p95 is None or after_p95 is None:
        return attach_http_p95(evaluation, None)
    updated = attach_http_p95(evaluation, improvement_ratio(before_p95, after_p95))
    updated["http_p95_ms"] = {"before": before_p95, "after": after_p95}
    return updated


def _with_plan_ms(explains: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**item, "actual_total_ms": plan_execution_ms(item.get("plan"))} for item in explains]


def _connected_report(
    *,
    existing: list[dict[str, Any]],
    explains: list[dict[str, Any]],
    explains_after: list[dict[str, Any]],
    builds: list[dict[str, Any]],
    writes: dict[str, Any],
) -> dict[str, Any]:
    build_seconds = {item["name"]: item["seconds"] for item in builds}
    write_ok = bool(writes.get("within_tolerance"))
    candidates = [
        _candidate_report(
            item,
            IndexGateInput(
                existing_index_covers=existing_covers_candidate(existing, candidate_columns(item)),
                p95_improvement=None,
                write_within_tolerance=write_ok,
                build_seconds=build_seconds.get(item["name"]),
            ),
        )
        for item in CANDIDATE_INDEXES
    ]
    return {
        "skipped": False,
        "indexes_submitted": False,
        "existing_indexes": [item["name"] for item in existing],
        "existing_index_defs": existing,
        "existing_covers_candidates": all(
            item["gate"]["existing_index_covers"] for item in candidates
        ),
        "explains": _with_plan_ms(explains),
        "explains_after": _with_plan_ms(explains_after),
        "builds": builds,
        "writes": writes,
        "candidates": candidates,
        "reason": (
            "EXPLAIN, transactional CREATE INDEX (rolled back), and write "
            "probes were captured. Three-round HTTP market p95 is still "
            "missing; candidates stay out"
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


async def _create_candidate_indexes(connection: Any) -> list[dict[str, Any]]:
    import time

    from sqlalchemy import text

    builds: list[dict[str, Any]] = []
    for item in CANDIDATE_INDEXES:
        started = time.perf_counter()
        await connection.execute(text(item["ddl"]))
        builds.append({"name": item["name"], "seconds": time.perf_counter() - started})
    return builds


async def _time_writes(connection: Any, start: int) -> list[float]:
    import time

    from sqlalchemy import text

    insert = text(WRITE_INSERT_SQL)
    samples: list[float] = []
    for offset in range(WRITE_PROBE_COUNT):
        started = time.perf_counter()
        await connection.execute(
            insert,
            {
                "url": f"{WRITE_URL_PREFIX}/{start + offset}",
                "title": f"perf-index-write-{start + offset}",
            },
        )
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def _write_probe_report(before: list[float], after: list[float]) -> dict[str, Any]:
    before_p95 = percentile_block(before)["p95"]
    after_p95 = percentile_block(after)["p95"]
    return {
        "before_p95_ms": before_p95,
        "after_p95_ms": after_p95,
        "count": WRITE_PROBE_COUNT,
        "within_tolerance": write_p95_within_tolerance(before_p95, after_p95),
    }


async def _evaluate_connected(connection: Any) -> dict[str, Any]:
    try:
        explains, existing = await _explain_market_queries(connection)
        before = await _time_writes(connection, 0)
        builds = await _create_candidate_indexes(connection)
        after = await _time_writes(connection, 1000)
        explains_after, _existing_after = await _explain_market_queries(connection)
        return _connected_report(
            existing=existing,
            explains=explains,
            explains_after=explains_after,
            builds=builds,
            writes=_write_probe_report(before, after),
        )
    finally:
        await connection.rollback()


def drop_index_sql(name: str) -> str:
    """Forward drop only. Names come from the checked-in candidate list."""
    if name not in {item["name"] for item in CANDIDATE_INDEXES}:
        raise ValueError(f"refusing to drop unknown index {name}")
    return f"DROP INDEX IF EXISTS {name}"


async def _run_isolated_ddl(statements: list[str]) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from modules.config import get_settings
    from scripts.perf.env import assert_isolated_target

    settings = get_settings()
    assert_isolated_target(settings)
    engine = create_async_engine(settings.database_url, pool_size=1)
    try:
        async with engine.begin() as connection:
            for statement in statements:
                await connection.execute(text(statement))
    finally:
        await engine.dispose()


async def commit_candidate_indexes() -> list[str]:
    """Make candidates visible to HTTP measure connections. Always pair with drop."""
    await _run_isolated_ddl([item["ddl"] for item in CANDIDATE_INDEXES])
    return [item["name"] for item in CANDIDATE_INDEXES]


async def drop_candidate_indexes() -> list[str]:
    """Remove measurement-only indexes. Never used as an Alembic downgrade."""
    names = [item["name"] for item in CANDIDATE_INDEXES]
    await _run_isolated_ddl([drop_index_sql(name) for name in names])
    return names


async def evaluate_market_indexes() -> dict[str, Any]:
    """EXPLAIN, time writes, and build candidates inside one rolled-back transaction.

    When PostgreSQL is unreachable, return a skipped report. Never create an
    Alembic revision from a skipped or failed-gate result. HTTP three-round
    p95 is attached later with ``attach_http_p95``.
    """
    try:
        from sqlalchemy.ext.asyncio import create_async_engine

        from modules.config import get_settings
        from scripts.perf.env import assert_isolated_target
    except Exception as exc:  # pragma: no cover - import surface only
        return skipped_evaluation(f"imports unavailable: {exc}")

    engine = None
    try:
        settings = get_settings()
        assert_isolated_target(settings)
        engine = create_async_engine(settings.database_url, pool_size=1)
        async with engine.connect() as connection:
            return await _evaluate_connected(connection)
    except Exception as exc:
        return skipped_evaluation(str(exc))
    finally:
        if engine is not None:
            await engine.dispose()
