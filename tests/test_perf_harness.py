"""Deterministic coverage for the isolated performance harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.perf.catalog_bytes import catalog_byte_report, compact_bytes, pick_namespaces
from scripts.perf.download_bench import compare_chunks, copy_payload
from scripts.perf.env import (
    PERF_DATABASE,
    PERF_REDIS_PREFIX,
    apply_isolated_env,
    assert_isolated_target,
    isolated_environ,
    ports_from_environ,
)
from scripts.perf.profiles import (
    CACHE_PRUNE_SCANS_TODAY,
    CURRENT_DOWNLOAD_CHUNK,
    DOWNLOAD_CHUNK_CANDIDATES,
    FLEETS,
    LOGIN_NAMESPACES,
    OVERVIEW_NAMESPACES,
    OVERVIEW_SERVER_LIMIT,
    actor_cycle,
    event_count,
    history_kind_for_index,
    overview_counted_servers,
    retained_counts,
    split_ownership,
)
from scripts.perf.report import (
    compare_summaries,
    empty_report,
    improvement_ratio,
    locale_bytes_pass,
    median_of_rounds,
    meets_improvement,
    percentile_block,
    regression_limit_ms,
    soak_holds_rss,
    within_regression,
    within_resource_gate,
    write_report,
)
from scripts.perf.stubs import StubServer, stub_payload


def test_fleet_profiles_match_the_agreed_tiers():
    assert FLEETS["fleet-10"].servers == 10
    assert FLEETS["fleet-10"].online_users == 1
    assert FLEETS["fleet-100"].servers == 100
    assert FLEETS["fleet-100"].online_users == 10
    assert FLEETS["fleet-500"].servers == 500
    assert FLEETS["fleet-500"].online_users == 30
    assert FLEETS["fleet-1000"].compatibility is True
    assert FLEETS["fleet-1001"].servers == 1001


def test_ownership_covers_admin_member_and_disjoint_tenants():
    for total in (4, 10, 100, 500, 1000, 1001):
        split = split_ownership(total)
        assert split.total == total
        assert split.admin >= 1
        assert split.member >= 1
        assert split.tenant_a == 1
        assert split.tenant_b == 1


def test_overview_keeps_the_existing_thousand_server_cap():
    assert overview_counted_servers(1000) == OVERVIEW_SERVER_LIMIT
    assert overview_counted_servers(1001) == OVERVIEW_SERVER_LIMIT
    assert overview_counted_servers(10) == 10


def test_actor_cycle_reuses_admin_for_extra_online_users():
    assert actor_cycle(1) == ("admin",)
    assert actor_cycle(4) == ("admin", "member", "tenant_a", "tenant_b")
    assert actor_cycle(6).count("admin") == 3


def test_history_mix_covers_empty_daily_and_retention_cap():
    kinds = {history_kind_for_index(index, 20, "daily") for index in range(20)}
    assert kinds == {"empty", "daily", "max"}
    assert history_kind_for_index(0, 5, "empty") == "empty"
    assert retained_counts("empty") == (0, 0, 0, 0)
    assert retained_counts("max") == (1, 10, 100, 100)
    assert event_count("max", "running") == 300
    assert event_count("daily", "completed") == 1


def test_gates_use_stated_thresholds_not_claimed_gains():
    assert meets_improvement(100.0, 80.0) is True
    assert meets_improvement(100.0, 85.0) is False
    assert improvement_ratio(100.0, 80.0) == pytest.approx(0.20)
    assert regression_limit_ms(100.0) == 120.0
    assert regression_limit_ms(1000.0) == 1050.0
    assert within_regression(100.0, 119.0) is True
    assert within_regression(100.0, 121.0) is False
    assert within_resource_gate(100.0, 105.0) is True
    assert within_resource_gate(100.0, 106.0) is False
    assert soak_holds_rss([100, 105, 104]) is True
    assert soak_holds_rss([197, 227, 230]) is True
    assert soak_holds_rss([100, 105, 112]) is False
    assert soak_holds_rss([100, 110]) is False
    assert soak_holds_rss([100]) is False
    assert locale_bytes_pass(1000, 500) is True
    assert locale_bytes_pass(1000, 501) is False


def test_median_of_three_rounds_picks_the_middle_value():
    assert median_of_rounds([30.0, 10.0, 20.0]) == 20.0
    assert median_of_rounds([]) == 0.0


def test_percentile_block_matches_in_process_observability():
    from modules.observability.stats import percentile_block as shared

    values = [float(item) for item in range(1, 101)]
    assert percentile_block(values) == shared(values)


def test_isolated_ports_keep_defaults_and_honor_host_overrides():
    defaults = ports_from_environ({})
    assert defaults.postgres == 55432
    assert defaults.redis == 56379
    override = ports_from_environ(
        {
            "UPKK_PERF_POSTGRES_PORT": "55442",
            "UPKK_PERF_REDIS_PORT": "56389",
        }
    )
    values = isolated_environ(override)
    assert values["POSTGRES_PORT"] == "55442"
    assert values["REDIS_PORT"] == "56389"
    assert values["POSTGRES_HOST"] == "127.0.0.1"
    assert values["POSTGRES_DATABASE"] == PERF_DATABASE
    assert values["REDIS_KEY_PREFIX"] == PERF_REDIS_PREFIX
    with pytest.raises(ValueError, match="UPKK_PERF_POSTGRES_PORT"):
        ports_from_environ({"UPKK_PERF_POSTGRES_PORT": "0"})


def test_isolated_env_rejects_production_targets(tmp_path: Path):
    values = apply_isolated_env(target={})
    assert values["POSTGRES_DATABASE"] == PERF_DATABASE
    assert values["REDIS_KEY_PREFIX"] == PERF_REDIS_PREFIX
    assert_isolated_target(isolated_environ())
    with pytest.raises(SystemExit):
        assert_isolated_target({"POSTGRES_DATABASE": "cs2_manager", "REDIS_KEY_PREFIX": "perf:"})
    with pytest.raises(SystemExit):
        assert_isolated_target({"POSTGRES_DATABASE": PERF_DATABASE, "REDIS_KEY_PREFIX": ""})
    report = empty_report(profile="fleet-10", mode="smoke", cwd=tmp_path)
    assert report["claimed_gains"] is False
    path = tmp_path / "report.json"
    from datetime import UTC, datetime

    write_report(path, {**report, "panel": {"captured_at": datetime(2026, 9, 14, tzinfo=UTC)}})
    assert json.loads(path.read_text())["format"] == "upkk-isolated-perf"
    assert json.loads(path.read_text())["panel"]["captured_at"].endswith("Z")


def test_catalog_subsets_are_smaller_than_the_full_client_payload():
    report = catalog_byte_report()
    english = report["locales"]["en-US"]
    chinese = report["locales"]["zh-CN"]
    assert english["full"] > 100_000
    assert chinese["full"] > 100_000
    assert english["login_subset"] < english["full"] / 2
    assert english["overview_subset"] < english["full"] / 2
    assert report["client_estimate"]["login"] == english["login_subset"]
    assert report["client_estimate"]["overview"] == english["overview_subset"]
    picked = pick_namespaces(
        {"feedback": {"a": "b"}, "login": {"c": "d"}, "other": 1}, LOGIN_NAMESPACES
    )
    assert picked == {"feedback": {"a": "b"}, "login": {"c": "d"}}
    nested = pick_namespaces(
        {
            "plugins": {"aiImport": {"x": 1}, "other": 2},
            "feedback": {"ok": "OK"},
        },
        ("feedback", "plugins.aiImport"),
    )
    assert nested == {"feedback": {"ok": "OK"}, "plugins": {"aiImport": {"x": 1}}}
    assert compact_bytes({"a": 1}) == len(b'{"a":1}')


def test_compare_summaries_leave_unverified_when_measurements_are_missing():
    baseline = empty_report(profile="fleet-10", mode="smoke")
    candidate = empty_report(profile="fleet-10", mode="smoke")
    gates = compare_summaries(baseline, candidate)
    assert gates["inbox_p95_improvement"]["verified"] is False
    assert gates["locale_login_bytes"]["verified"] is False


def test_download_chunk_write_counts_match_payload_size(tmp_path: Path):
    from scripts.perf.download_bench import CountingWriter

    payload = b"x" * 65536
    writer = CountingWriter(tmp_path / "out.bin")

    async def _run() -> None:
        await writer.open()
        await copy_payload(payload, 8192, writer)
        await writer.aclose()

    import asyncio

    asyncio.run(_run())
    assert writer.writes == 8
    assert CURRENT_DOWNLOAD_CHUNK == 8192
    from modules.http_helper import DOWNLOAD_CHUNK_SIZE

    assert DOWNLOAD_CHUNK_SIZE == CURRENT_DOWNLOAD_CHUNK
    assert DOWNLOAD_CHUNK_CANDIDATES == (8192, 65536, 262144)
    assert CACHE_PRUNE_SCANS_TODAY == 1


@pytest.mark.asyncio
async def test_download_bench_records_all_candidates_without_adopting_them():
    from scripts.perf.download_bench import adopted_chunk_size

    rows = await compare_chunks(payload_bytes=64 * 1024)
    assert [row["chunk_size"] for row in rows] == list(DOWNLOAD_CHUNK_CANDIDATES)
    eight_k = next(row for row in rows if row["chunk_size"] == 8192)
    assert eight_k["writes"] == 8
    chosen = adopted_chunk_size(rows)
    assert all((row["adopted"] is (row["chunk_size"] == chosen and chosen != 8192)) for row in rows)


def test_stub_payloads_and_loopback_health():
    assert stub_payload("github")["full_name"] == "perf-fixture/plugin"
    server = StubServer(port=0)
    server.start()
    try:
        import urllib.request

        with urllib.request.urlopen(f"{server.origin}/health", timeout=2) as response:
            body = json.loads(response.read().decode())
        assert body == {"status": "ok"}
        with urllib.request.urlopen(
            f"{server.origin}/github?mode=delay&delay=0", timeout=2
        ) as response:
            github = json.loads(response.read().decode())
        assert github["name"] == "fixture-plugin"
    finally:
        server.stop()


def test_overview_namespaces_do_not_include_marketplace_copy():
    assert "plugins" not in OVERVIEW_NAMESPACES
    assert "login" in LOGIN_NAMESPACES


def test_chunk_adoption_fails_closed_and_requires_every_gate():
    from scripts.perf.download_bench import adopted_chunk_size

    baseline = {
        "chunk_size": 8192,
        "throughput_bps": 100.0,
        "cancel_ms": 10.0,
        "peak_bytes": 1000.0,
        "loop_p95_ms": 1.0,
    }
    assert adopted_chunk_size([baseline]) == 8192
    winner = {
        "chunk_size": 65536,
        "throughput_bps": 120.0,
        "cancel_ms": 11.0,
        "peak_bytes": 1040.0,
        "loop_p95_ms": 1.0,
    }
    assert adopted_chunk_size([baseline, winner]) == 65536
    assert adopted_chunk_size([baseline, {**winner, "throughput_bps": 109.0}]) == 8192
    assert adopted_chunk_size([baseline, {**winner, "peak_bytes": 1100.0}]) == 8192


def test_market_index_gates_fail_closed_without_measurements():
    from scripts.perf.index_eval import IndexGateInput, index_inclusion_passes, skipped_evaluation

    assert (
        index_inclusion_passes(
            IndexGateInput(
                existing_index_covers=False,
                p95_improvement=None,
                write_within_tolerance=False,
                build_seconds=None,
            )
        )
        is False
    )
    assert (
        index_inclusion_passes(
            IndexGateInput(
                existing_index_covers=False,
                p95_improvement=0.20,
                write_within_tolerance=True,
                build_seconds=5.0,
            )
        )
        is True
    )
    assert (
        index_inclusion_passes(
            IndexGateInput(
                existing_index_covers=True,
                p95_improvement=0.50,
                write_within_tolerance=True,
                build_seconds=1.0,
            )
        )
        is False
    )
    assert (
        index_inclusion_passes(
            IndexGateInput(
                existing_index_covers=False,
                p95_improvement=0.19,
                write_within_tolerance=True,
                build_seconds=1.0,
            )
        )
        is False
    )
    skipped = skipped_evaluation("docker unavailable")
    assert skipped["indexes_submitted"] is False
    assert skipped["skipped"] is True
    assert all(item["included"] is False for item in skipped["candidates"])


def test_index_eval_sql_uses_stored_enum_names():
    from scripts.perf.index_eval import TARGET_QUERIES

    assert all("COUNTERSTRIKESHARP" in item["sql"] for item in TARGET_QUERIES)


def test_index_eval_queries_match_http_list_shape():
    from scripts.perf.index_eval import TARGET_QUERIES

    by_name = {item["name"]: item["sql"] for item in TARGET_QUERIES}
    assert "description_i18n" in by_name["market_recommended"]
    assert "LIMIT 20 OFFSET 0" in by_name["market_recommended"]
    assert "LIMIT 20 OFFSET 40" in by_name["market_oldest_page"]
    assert "count(*)" in by_name["market_count"]
    assert "SELECT id FROM" not in by_name["market_recommended"]
    assert "category = 'UTILITY'" in by_name["market_recommended_utility"]


def test_index_coverage_uses_column_prefix_not_names():
    from scripts.perf.index_eval import (
        btree_covers,
        existing_covers_candidate,
        parse_index_columns,
    )

    primary = parse_index_columns(
        "CREATE UNIQUE INDEX pk_market_plugins ON public.market_plugins USING btree (id)"
    )
    title = parse_index_columns(
        "CREATE INDEX ix_market_plugins_title ON public.market_plugins USING btree (title)"
    )
    recommended = parse_index_columns(
        "CREATE INDEX ix_market_plugins_framework_recommended_installs_created "
        "ON public.market_plugins USING btree "
        "(framework, is_recommended, install_count, created_at DESC, id DESC)"
    )
    assert primary == ("id",)
    assert title == ("title",)
    assert recommended == (
        "framework",
        "is_recommended",
        "install_count",
        "created_at",
        "id",
    )
    assert btree_covers(
        recommended, ("framework", "is_recommended", "install_count", "created_at", "id")
    )
    assert not btree_covers(primary, ("framework", "created_at", "id"))
    existing = [
        {"name": "pk_market_plugins", "columns": primary},
        {"name": "ix_market_plugins_title", "columns": title},
    ]
    assert existing_covers_candidate(existing, ("framework", "created_at", "id")) is False
    assert existing_covers_candidate(
        [{"name": "dup", "columns": recommended}],
        ("framework", "is_recommended", "install_count", "created_at", "id"),
    )


def test_drop_index_sql_only_allows_checked_in_candidates():
    from scripts.perf.index_eval import CANDIDATE_INDEXES, drop_index_sql

    name = CANDIDATE_INDEXES[0]["name"]
    assert drop_index_sql(name) == f"DROP INDEX IF EXISTS {name}"
    with pytest.raises(ValueError, match="unknown index"):
        drop_index_sql("ix_not_a_candidate")


def test_write_probe_sql_fills_not_null_market_columns():
    from scripts.perf.index_eval import WRITE_INSERT_SQL

    assert "is_recommended" in WRITE_INSERT_SQL
    assert "install_count" in WRITE_INSERT_SQL
    assert "download_count" in WRITE_INSERT_SQL


def test_write_tolerance_matches_regression_envelope():
    from scripts.perf.index_eval import write_p95_within_tolerance

    assert write_p95_within_tolerance(10.0, 30.0) is True
    assert write_p95_within_tolerance(10.0, 31.0) is False
    assert write_p95_within_tolerance(1000.0, 1050.0) is True
    assert write_p95_within_tolerance(1000.0, 1051.0) is False


def test_plan_execution_ms_reads_actual_total():
    from scripts.perf.index_eval import plan_execution_ms

    assert plan_execution_ms([{"Plan": {"Actual Total Time": 1.25}}]) == 1.25
    assert plan_execution_ms({"Plan": {"Actual Total Time": 2.0}}) == 2.0
    assert plan_execution_ms({}) is None
    assert plan_execution_ms("seq") is None


def test_attach_http_from_reports_reads_market_median_p95():
    from scripts.perf.index_eval import (
        CANDIDATE_INDEXES,
        attach_http_from_reports,
        market_p95_from_report,
    )

    before = {"api": {"summary": {"market": {"latency_ms": {"p95": 7.5}}}}}
    after = {"api": {"summary": {"market": {"latency_ms": {"p95": 5.0}}}}}
    assert market_p95_from_report(before) == 7.5
    assert market_p95_from_report({}) is None
    ready = {
        "reason": "waiting",
        "candidates": [
            {
                "name": item["name"],
                "purpose": item["purpose"],
                "ddl": item["ddl"],
                "columns": item["columns"],
                "included": False,
                "gate": {
                    "existing_index_covers": False,
                    "p95_improvement": None,
                    "write_within_tolerance": True,
                    "build_seconds": 0.01,
                },
            }
            for item in CANDIDATE_INDEXES
        ],
    }
    decided = attach_http_from_reports(ready, before, after)
    assert decided["http_p95_ms"] == {"before": 7.5, "after": 5.0}
    assert decided["indexes_submitted"] is True
    assert attach_http_from_reports(ready, before, {"api": {}})["indexes_submitted"] is False


def test_attach_http_p95_fails_closed_until_every_gate_passes():
    from scripts.perf.index_eval import CANDIDATE_INDEXES, attach_http_p95, skipped_evaluation

    skipped = attach_http_p95(skipped_evaluation("offline"), 0.50)
    assert skipped["indexes_submitted"] is False
    assert skipped["http_p95_improvement"] == 0.50

    ready = {
        "reason": "waiting for HTTP",
        "candidates": [
            {
                "name": item["name"],
                "purpose": item["purpose"],
                "ddl": item["ddl"],
                "columns": item["columns"],
                "included": False,
                "gate": {
                    "existing_index_covers": False,
                    "p95_improvement": None,
                    "write_within_tolerance": True,
                    "build_seconds": 0.01,
                },
            }
            for item in CANDIDATE_INDEXES
        ],
    }
    included = attach_http_p95(ready, 0.25)
    assert included["indexes_submitted"] is True
    assert all(item["included"] for item in included["candidates"])
    rejected = attach_http_p95(ready, 0.10)
    assert rejected["indexes_submitted"] is False
    assert all(item["included"] is False for item in rejected["candidates"])


def test_selected_routes_keep_catalog_order_and_reject_unknown_names():
    from scripts.perf.api_measure import ROUTES, selected_routes

    assert selected_routes(None) == ROUTES
    assert selected_routes([]) == ROUTES
    isolated = selected_routes(["servers", "overview", "overview"])
    assert [name for name, _path in isolated] == ["overview", "servers"]
    with pytest.raises(ValueError, match="unknown measurement routes"):
        selected_routes(["inbox", "not-a-route"])
