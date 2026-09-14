"""HTTP/ASGI measurement against the isolated panel. No new public metrics API."""

from __future__ import annotations

import asyncio
import resource
from collections import defaultdict
from time import monotonic, perf_counter
from typing import Any

import httpx

from modules.observability import set_enabled, snapshot_counts
from scripts.perf.profiles import MEASURES, MeasureMode, actor_cycle
from scripts.perf.report import attach_route_summary, median_of_rounds, percentile_block
from services.panel_metrics import capture_panel_performance

ROUTES = (
    ("inbox", "/api/v1/operations/inbox"),
    ("overview", "/api/v1/overview/summary"),
    ("market", "/api/v1/plugins/market?framework=counterstrikesharp&limit=20&offset=0"),
    ("servers", "/api/v1/servers"),
)


def _auth(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def probe_inbox(client: httpx.AsyncClient, token: str) -> dict[str, Any]:
    before = snapshot_counts()
    started = perf_counter()
    response = await client.get("/api/v1/operations/inbox", headers=_auth(token))
    elapsed_ms = (perf_counter() - started) * 1000
    after = snapshot_counts()
    return {
        "status": response.status_code,
        "latency_ms": round(elapsed_ms, 3),
        "sql": after.get("db.executions", 0) - before.get("db.executions", 0),
        "redis": after.get("redis.ops", 0) - before.get("redis.ops", 0),
        "bytes": len(response.content),
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
) -> None:
    while monotonic() < stop_at:
        for name, path in ROUTES:
            await _one_request(client, token, path, samples, errors, counts, name, record)
            if monotonic() >= stop_at:
                return


def _rss_bytes() -> int:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss = int(usage.ru_maxrss)
    if rss > 10_000_000:
        return rss
    return rss * 1024


async def run_api_rounds(
    *,
    tokens: dict[str, str],
    online_users: int,
    mode: MeasureMode,
    transport: httpx.AsyncBaseTransport | None,
    base_url: str,
) -> dict[str, Any]:
    set_enabled(True)
    spec = MEASURES[mode]
    roles = actor_cycle(online_users)
    rounds: list[dict[str, Any]] = []
    async with httpx.AsyncClient(transport=transport, base_url=base_url, timeout=30.0) as client:
        probe = await probe_inbox(client, tokens["admin"])
        for index in range(spec.api_rounds):
            rounds.append(
                await _one_round(
                    client, tokens, roles, spec.api_warmup_seconds, spec.api_measure_seconds
                )
            )
            rounds[-1]["round"] = index + 1
    summary = _summarize_rounds(rounds)
    backend = await capture_panel_performance()
    return {
        "probes": {"inbox": probe},
        "rounds": rounds,
        "summary": summary,
        "backend": {
            "rss_bytes": _rss_bytes(),
            "panel": backend,
        },
    }


async def _one_round(
    client: httpx.AsyncClient,
    tokens: dict[str, str],
    roles: tuple[str, ...],
    warmup_seconds: int,
    measure_seconds: int,
) -> dict[str, Any]:
    samples: dict[str, list[float]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    await _run_window(client, tokens, roles, warmup_seconds, samples, errors, counts, False)
    started = monotonic()
    await _run_window(client, tokens, roles, measure_seconds, samples, errors, counts, True)
    elapsed = max(0.001, monotonic() - started)
    routes = {}
    for name, _path in ROUTES:
        payload = attach_route_summary(samples[name], errors=errors[name], count=counts[name])
        payload["throughput_rps"] = round(counts[name] / elapsed, 3)
        payload["latency_ms"] = percentile_block(samples[name])
        routes[name] = payload
    return {"elapsed_s": round(elapsed, 3), "routes": routes, "rss_bytes": _rss_bytes()}


async def _run_window(
    client: httpx.AsyncClient,
    tokens: dict[str, str],
    roles: tuple[str, ...],
    seconds: int,
    samples: dict[str, list[float]],
    errors: dict[str, int],
    counts: dict[str, int],
    record: bool,
) -> None:
    if seconds <= 0:
        return
    stop_at = monotonic() + seconds
    tasks = [
        asyncio.create_task(_worker(client, tokens[role], stop_at, samples, errors, counts, record))
        for role in roles
    ]
    await asyncio.gather(*tasks)


def _summarize_rounds(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for name, _path in ROUTES:
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
