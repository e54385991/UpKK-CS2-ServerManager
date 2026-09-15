"""Deterministic coverage for this round's measurement protocol."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.perf.arrival import (
    ArrivalStats,
    arrival_offsets,
    arrival_report,
    error_free_rates,
    load_arrival_targets,
    run_paced_route,
    scale_rates,
)
from scripts.perf.cases import COMPATIBILITY_FLEETS, PRIMARY_FLEETS, measurement_matrix
from scripts.perf.fingerprint import capture_fingerprint, compose_images, file_digest
from scripts.perf.profiles import ARRIVAL_RATIO, FLEETS, ROUND_START_SHA
from scripts.perf.realtime import (
    EVENT_CYCLE,
    ParsedEvent,
    ScriptedLifecycle,
    next_cycle_step,
    parse_sse_chunk,
    run_realtime_rounds,
)
from scripts.perf.segments import (
    SEGMENT_KEYS,
    count_delta,
    empty_segments,
    latest_sample,
    panel_checked_out,
    segments_from_counts,
)


def test_cli_accepts_fingerprint_realtime_and_paced_flags():
    from scripts.perf.cli import parse_args

    paced = parse_args(["measure-api", "--paced", "--arrival-from", "reports/perf/raw/prior.json"])
    assert paced.command == "measure-api"
    assert paced.paced is True
    assert paced.arrival_from.endswith("prior.json")
    indexed = parse_args(["measure-api", "--with-index-candidates", "--route", "market"])
    assert indexed.with_index_candidates is True
    assert indexed.routes == ["market"]
    decide = parse_args(
        [
            "explain-indexes",
            "--eval-from",
            "reports/perf/raw/explain-indexes-fleet-500.json",
            "--http-before",
            "before.json",
            "--http-after",
            "after.json",
        ]
    )
    assert decide.eval_from.endswith("explain-indexes-fleet-500.json")
    assert decide.http_before == "before.json"
    assert decide.http_after == "after.json"
    realtime = parse_args(["measure-realtime", "--sessions", "30", "--seconds", "15"])
    assert realtime.command == "measure-realtime"
    assert realtime.sessions == 30
    assert parse_args(["fingerprint"]).command == "fingerprint"


def test_this_round_starts_at_the_agreed_sha():
    assert ROUND_START_SHA.startswith("21197fe")
    assert ARRIVAL_RATIO == 0.80
    assert PRIMARY_FLEETS == ("fleet-10", "fleet-100", "fleet-500")
    assert COMPATIBILITY_FLEETS == ("fleet-1000", "fleet-1001")
    assert FLEETS["fleet-500"].online_users == 30


def test_measurement_matrix_covers_users_markets_and_browser_surfaces():
    matrix = measurement_matrix()
    assert matrix["history"] == ["empty", "daily", "max"]
    assert matrix["actors"] == ["admin", "member", "tenant_a", "tenant_b"]
    assert matrix["realtime_sessions"] == 30
    assert "activity-tray" in matrix["browser_routes"]
    assert matrix["browser_widths"] == [390, 1440]


def test_arrival_targets_are_eighty_percent_of_error_free_rates():
    discovered = error_free_rates(
        {
            "inbox": {"throughput_rps": 10.0, "errors": 0},
            "overview": {"throughput_rps": 20.0, "errors": 0},
        }
    )
    assert discovered == {"inbox": 10.0, "overview": 20.0}
    assert scale_rates(discovered or {}) == {"inbox": 8.0, "overview": 16.0}
    assert error_free_rates({"inbox": {"throughput_rps": 10.0, "errors": 1}}) is None


def test_arrival_offsets_stay_inside_the_window():
    offsets = arrival_offsets(4.0, 1.0)
    assert offsets == [0.0, 0.25, 0.5, 0.75]
    assert arrival_offsets(0.0, 1.0) == []
    assert arrival_offsets(4.0, 0.0) == []


@pytest.mark.asyncio
async def test_paced_route_records_late_emits_and_missed_slots():
    clock = {"t": 0.0}
    emitted: list[float] = []

    async def emit(_name: str, _path: str) -> None:
        emitted.append(clock["t"])
        clock["t"] += 0.8

    async def sleeper(delay: float) -> None:
        clock["t"] += delay

    stats = await run_paced_route(
        name="inbox",
        path="/inbox",
        target_rps=2.0,
        duration_s=1.0,
        emit=emit,
        now=lambda: clock["t"],
        sleeper=sleeper,
    )
    assert stats.scheduled == 2
    assert stats.emitted == 2
    assert stats.late == 1
    assert stats.missed == 0
    assert emitted == [0.0, 0.8]

    clock["t"] = 0.0
    emitted.clear()

    async def slow(_name: str, _path: str) -> None:
        emitted.append(clock["t"])
        clock["t"] += 2.0

    missed = await run_paced_route(
        name="inbox",
        path="/inbox",
        target_rps=10.0,
        duration_s=0.5,
        emit=slow,
        now=lambda: clock["t"],
        sleeper=sleeper,
    )
    assert missed.emitted == 1
    assert missed.missed >= 1
    assert missed.scheduled == 5


def test_arrival_report_reuses_targets_from_a_previous_file(tmp_path: Path):
    report = {
        "arrival": {
            "targets": {"inbox": 8.0, "overview": 16.0},
        }
    }
    path = tmp_path / "prior.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    loaded = load_arrival_targets(json.loads(path.read_text(encoding="utf-8")))
    assert loaded == {"inbox": 8.0, "overview": 16.0}
    payload = arrival_report(
        discovered={"inbox": 10.0},
        targets=loaded or {},
        stats={"inbox": ArrivalStats(8, 8, 0, 1, (5.0,))},
        reused=True,
    )
    assert payload["reused"] is True
    assert payload["ratio"] == 0.80
    assert payload["routes"]["inbox"]["late"] == 1


def test_segments_record_known_counters_and_leave_later_timings_empty():
    empty = empty_segments()
    assert set(empty) == set(SEGMENT_KEYS)
    assert empty["snapshot_ms"] is None
    built = segments_from_counts(
        {"db.executions": 2, "redis.ops": 1},
        {"db.executions": 5, "redis.ops": 4},
        sql_ms=12.0,
        redis_ms=3.0,
        db_checked_out=1,
        loop_lag_ms=0.4,
    )
    assert built["sql_count"] == 3
    assert built["redis_ops"] == 3
    assert built["sql_ms"] == 12.0
    assert built["json_ms"] is None
    assert count_delta({"a": 1}, {"a": 4}, "a") == 3
    assert latest_sample({"db.execute_ms": [1.0, 4.0]}, "db.execute_ms") == 4.0
    assert panel_checked_out({"database": {"checked_out": 2}}) == 2


def test_fingerprint_omits_secrets_and_reads_compose_pins():
    digest = file_digest(Path("uv.lock"))
    assert digest is not None
    assert len(digest) == 64
    images = compose_images("image: postgres:18.6-alpine\n  image: redis:8.10.1-alpine\n")
    assert images == ["postgres:18.6-alpine", "redis:8.10.1-alpine"]
    captured = capture_fingerprint()
    dumped = json.dumps(captured)
    assert "SECRET_KEY" not in dumped
    assert "POSTGRES_PASSWORD" not in dumped
    assert captured["starting_sha"] == ROUND_START_SHA
    assert captured["protocol"]["arrival_ratio"] == 0.80
    assert captured["protocol"]["claimed_gains"] is False
    assert captured["isolated"]["postgres_database"] == "cs2_perf"
    assert isinstance(captured["isolated"]["postgres_isolated"], bool)
    assert isinstance(captured["isolated"]["redis_isolated"], bool)


def test_sse_parser_keeps_inbox_events_and_keep_alives():
    events = parse_sse_chunk('event: inbox\ndata: {"a":1}\n\n: keep-alive\n\n')
    assert events[0] == ParsedEvent("inbox", len(b'{"a":1}'))
    assert events[1].name == "keep-alive"
    assert [next_cycle_step(index) for index in range(4)] == list(EVENT_CYCLE)


@pytest.mark.asyncio
async def test_realtime_sessions_see_injected_lifecycle_and_inbox_events():
    async def app(scope, receive, send):
        assert scope["type"] == "http"
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"event: inbox\ndata: {}\n\n",
                "more_body": False,
            }
        )

    injector = ScriptedLifecycle()
    from httpx import ASGITransport

    measured = await run_realtime_rounds(
        token="perf-token",
        sessions=2,
        seconds=1,
        transport=ASGITransport(app=app),
        base_url="http://perf.local",
        injector=injector,
    )
    assert measured["sessions"] == 2
    assert measured["errors"] == 0
    assert measured["events"].get("inbox", 0) >= 1
    assert measured["injected"]
    assert set(measured["injected"]) <= set(EVENT_CYCLE)
