"""HTTP/ASGI measurement against the isolated panel. No new public metrics API."""

from __future__ import annotations

import asyncio
import resource
from collections import defaultdict
from time import monotonic, perf_counter
from typing import Any, Sequence

import httpx

from modules.observability import set_enabled, snapshot_counts
from modules.observability.counters import loop_lag_latest, samples_items
from scripts.perf.arrival import (
    ArrivalStats,
    arrival_report,
    error_free_rates,
    run_paced_route,
    scale_rates,
)
from scripts.perf.profiles import ARRIVAL_RATIO, MEASURES, MeasureMode, actor_cycle
from scripts.perf.report import (
    attach_route_summary,
    median_of_rounds,
    percentile_block,
    soak_holds_rss,
)
from scripts.perf.segments import latest_sample, panel_checked_out, segments_from_counts
from services.panel_metrics import capture_panel_performance, database_pool_payload

ROUTES = (
    ("inbox", "/api/v1/operations/inbox"),
    ("overview", "/api/v1/overview/summary"),
    ("market", "/api/v1/plugins/market?framework=counterstrikesharp&limit=20&offset=0"),
    ("servers", "/api/v1/servers"),
)
ROUTE_NAMES = tuple(name for name, _path in ROUTES)


def selected_routes(names: Sequence[str] | None) -> tuple[tuple[str, str], ...]:
    """Keep catalog order. An empty selection means the mixed-load set."""
    if not names:
        return ROUTES
    wanted = list(dict.fromkeys(names))
    unknown = [name for name in wanted if name not in ROUTE_NAMES]
    if unknown:
        raise ValueError(f"unknown measurement routes: {', '.join(unknown)}")
    chosen = set(wanted)
    return tuple(item for item in ROUTES if item[0] in chosen)


def _auth(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def probe_inbox(client: httpx.AsyncClient, token: str) -> dict[str, Any]:
    before = snapshot_counts()
    started = perf_counter()
    response = await client.get("/api/v1/operations/inbox", headers=_auth(token))
    elapsed_ms = (perf_counter() - started) * 1000
    after = snapshot_counts()
    samples = dict(samples_items())
    return {
        "status": response.status_code,
        "latency_ms": round(elapsed_ms, 3),
        "sql": after.get("db.executions", 0) - before.get("db.executions", 0),
        "redis": after.get("redis.ops", 0) - before.get("redis.ops", 0),
        "bytes": len(response.content),
        "segments": segments_from_counts(
            before,
            after,
            sql_ms=latest_sample(samples, "db.execute_ms"),
            redis_ms=latest_sample(samples, "redis.op_ms"),
            db_checked_out=panel_checked_out({"database": database_pool_payload()}),
            loop_lag_ms=loop_lag_latest(),
        ),
    }


async def _one_request(
    client: httpx.AsyncClient,
    token: str,
    path: str,
    samples: dict[str, list[float]],
    errors: dict[str, int],
    counts: dict[str, int],
    name: str,
    record: bool,
) -> None:
    started = perf_counter()
    try:
        response = await client.get(path, headers=_auth(token))
        status = response.status_code
    except httpx.HTTPError:
        status = 599
    if not record:
        return
    elapsed = (perf_counter() - started) * 1000
    samples[name].append(elapsed)
    counts[name] += 1
    if status >= 400:
        errors[name] += 1


async def _worker(
    client: httpx.AsyncClient,
    token: str,
    stop_at: float,
    samples: dict[str, list[float]],
    errors: dict[str, int],
    counts: dict[str, int],
    record: bool,
    routes: tuple[tuple[str, str], ...],
) -> None:
    while monotonic() < stop_at:
        for name, path in routes:
            await _one_request(client, token, path, samples, errors, counts, name, record)
            if monotonic() >= stop_at:
                return


def _rss_bytes() -> int:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss = int(usage.ru_maxrss)
    if rss > 10_000_000:
        return rss
    return rss * 1024


def _current_rss_bytes() -> int:
    """Live RSS. ru_maxrss is a high-water mark and cannot show a soak plateau."""
    try:
        import os
        import subprocess

        raw = subprocess.check_output(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            text=True,
        ).strip()
        return int(raw.split()[0]) * 1024
    except OSError, ValueError, IndexError:
        return _rss_bytes()


async def run_api_rounds(
    *,
    tokens: dict[str, str],
    online_users: int,
    mode: MeasureMode,
    transport: httpx.AsyncBaseTransport | None,
    base_url: str,
    routes: Sequence[str] | None = None,
    paced: bool = False,
    arrival_targets: dict[str, float] | None = None,
) -> dict[str, Any]:
    set_enabled(True)
    spec = MEASURES[mode]
    roles = actor_cycle(online_users)
    catalog = selected_routes(routes)
    rounds: list[dict[str, Any]] = []
    reused = arrival_targets is not None
    discovered: dict[str, float] | None = None
    targets = dict(arrival_targets or {})
    async with httpx.AsyncClient(transport=transport, base_url=base_url, timeout=30.0) as client:
        probe = await probe_inbox(client, tokens["admin"])
        if paced and not targets:
            discovered = await _discover_rates(
                client, tokens, roles, spec.api_warmup_seconds, catalog
            )
            if discovered:
                targets = scale_rates(discovered, ARRIVAL_RATIO)
        for index in range(spec.api_rounds):
            rounds.append(
                await _one_round(
                    client,
                    tokens,
                    roles,
                    spec.api_warmup_seconds,
                    spec.api_measure_seconds,
                    catalog,
                    targets if paced else None,
                )
            )
            rounds[-1]["round"] = index + 1
    summary = _summarize_rounds(rounds, catalog)
    backend = await capture_panel_performance()
    last_arrival = rounds[-1].get("arrival") if rounds else {}
    return {
        "probes": {"inbox": probe},
        "isolation": [name for name, _path in catalog],
        "paced": paced,
        "arrival": arrival_report(
            discovered=discovered,
            targets=targets,
            stats=last_arrival if isinstance(last_arrival, dict) else {},
            reused=reused,
        ),
        "rounds": rounds,
        "summary": summary,
        "backend": {
            "rss_bytes": _rss_bytes(),
            "panel": backend,
        },
        "segments": probe.get("segments") or {},
    }


async def _discover_rates(
    client: httpx.AsyncClient,
    tokens: dict[str, str],
    roles: tuple[str, ...],
    seconds: int,
    routes: tuple[tuple[str, str], ...],
) -> dict[str, float] | None:
    samples: dict[str, list[float]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    started = monotonic()
    await _run_window(client, tokens, roles, seconds, samples, errors, counts, True, routes)
    elapsed = max(0.001, monotonic() - started)
    recorded = {}
    for name, _path in routes:
        recorded[name] = {
            "errors": errors[name],
            "throughput_rps": round(counts[name] / elapsed, 3),
        }
    return error_free_rates(recorded)


async def _one_round(
    client: httpx.AsyncClient,
    tokens: dict[str, str],
    roles: tuple[str, ...],
    warmup_seconds: int,
    measure_seconds: int,
    routes: tuple[tuple[str, str], ...],
    targets: dict[str, float] | None = None,
) -> dict[str, Any]:
    samples: dict[str, list[float]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    await _run_window(client, tokens, roles, warmup_seconds, samples, errors, counts, False, routes)
    started = monotonic()
    arrival: dict[str, Any] = {}
    if targets:
        paced = await _run_paced_window(
            client, tokens, roles, measure_seconds, samples, errors, counts, True, routes, targets
        )
        arrival = {name: row.as_dict() for name, row in paced.items()}
    else:
        await _run_window(
            client, tokens, roles, measure_seconds, samples, errors, counts, True, routes
        )
    elapsed = max(0.001, monotonic() - started)
    recorded = {}
    for name, _path in routes:
        payload = attach_route_summary(samples[name], errors=errors[name], count=counts[name])
        payload["throughput_rps"] = round(counts[name] / elapsed, 3)
        payload["latency_ms"] = percentile_block(samples[name])
        recorded[name] = payload
    return {
        "elapsed_s": round(elapsed, 3),
        "routes": recorded,
        "rss_bytes": _rss_bytes(),
        "arrival": arrival,
    }


async def _run_window(
    client: httpx.AsyncClient,
    tokens: dict[str, str],
    roles: tuple[str, ...],
    seconds: int,
    samples: dict[str, list[float]],
    errors: dict[str, int],
    counts: dict[str, int],
    record: bool,
    routes: tuple[tuple[str, str], ...],
) -> None:
    if seconds <= 0:
        return
    stop_at = monotonic() + seconds
    tasks = [
        asyncio.create_task(
            _worker(client, tokens[role], stop_at, samples, errors, counts, record, routes)
        )
        for role in roles
    ]
    await asyncio.gather(*tasks)


async def _run_paced_window(
    client: httpx.AsyncClient,
    tokens: dict[str, str],
    roles: tuple[str, ...],
    seconds: int,
    samples: dict[str, list[float]],
    errors: dict[str, int],
    counts: dict[str, int],
    record: bool,
    routes: tuple[tuple[str, str], ...],
    targets: dict[str, float],
) -> dict[str, ArrivalStats]:
    if seconds <= 0:
        return {name: ArrivalStats(0, 0, 0, 0, ()) for name, _path in routes}
    token_cycle = [tokens[role] for role in roles]
    cursor = 0

    async def emit(name: str, path: str) -> None:
        nonlocal cursor
        token = token_cycle[cursor % len(token_cycle)]
        cursor += 1
        await _one_request(client, token, path, samples, errors, counts, name, record)

    tasks = [
        run_paced_route(
            name=name,
            path=path,
            target_rps=float(targets.get(name) or 0.0),
            duration_s=float(seconds),
            emit=emit,
        )
        for name, path in routes
    ]
    rows = await asyncio.gather(*tasks)
    return {name: row for (name, _path), row in zip(routes, rows, strict=True)}


def _summarize_rounds(
    rounds: list[dict[str, Any]],
    routes: tuple[tuple[str, str], ...],
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for name, _path in routes:
        p95s = [
            float(round_row["routes"][name]["latency_ms"]["p95"])
            for round_row in rounds
            if name in round_row.get("routes", {})
        ]
        errors = sum(int(round_row["routes"][name]["errors"]) for round_row in rounds)
        count = sum(int(round_row["routes"][name]["count"]) for round_row in rounds)
        ranked_p95 = median_of_rounds(p95s)
        summary[name] = {
            "count": count,
            "errors": errors,
            "latency_ms": {"p95": ranked_p95, "round_p95_ms": p95s},
            "round_p95_ms": p95s,
        }
    return summary


async def run_soak(
    *,
    tokens: dict[str, str],
    online_users: int,
    transport: httpx.AsyncBaseTransport | None,
    base_url: str,
    routes: Sequence[str] | None = None,
    seconds: int,
    sample_every: int = 60,
) -> dict[str, Any]:
    set_enabled(True)
    catalog = selected_routes(routes)
    roles = actor_cycle(online_users)
    series: list[dict[str, Any]] = []
    probes: list[dict[str, Any]] = []
    samples: dict[str, list[float]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    chunk = max(1, sample_every)
    async with httpx.AsyncClient(transport=transport, base_url=base_url, timeout=30.0) as client:
        started = monotonic()
        while True:
            elapsed = monotonic() - started
            series.append(
                {
                    "t_s": round(elapsed, 1),
                    "rss_bytes": _current_rss_bytes(),
                    "rss_max_bytes": _rss_bytes(),
                }
            )
            probes.append(await probe_inbox(client, tokens["admin"]))
            remaining = seconds - (monotonic() - started)
            if remaining <= 0:
                break
            await _run_window(
                client,
                tokens,
                roles,
                min(chunk, max(1, int(remaining))),
                samples,
                errors,
                counts,
                False,
                catalog,
            )
    rss_values = [int(row["rss_bytes"]) for row in series]
    backend = await capture_panel_performance()
    return {
        "isolation": [name for name, _path in catalog],
        "seconds": seconds,
        "sample_every": chunk,
        "rss": series,
        "probes": {
            "inbox_first": probes[0] if probes else None,
            "inbox_last": probes[-1] if probes else None,
        },
        "rss_pass": soak_holds_rss(rss_values),
        "backend": {
            "rss_bytes": _rss_bytes(),
            "rss_current_bytes": _current_rss_bytes(),
            "panel": backend,
        },
    }
