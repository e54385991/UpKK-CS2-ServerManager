# Isolated performance measurement

This is the Stage 0 harness for the non-destructive full-stack performance
work. The numbers it produces are **measurements**, not accepted production
gains. Timing comparisons stay on a fixed isolated host; default CI keeps
deterministic call-count and protocol tests only.

`cacheComponents` and `partialPrefetching` remain off. This harness does not
change that gate.

## Isolated stack

PostgreSQL 18.6 and Redis 8.10.1 use dedicated volumes and loopback ports.
They never reuse the live panel volumes.

```bash
docker compose -f docker-compose.perf.yml up -d --wait
uv run python scripts/run_perf.py catalog-bytes
uv run python scripts/run_perf.py download-bench
uv run python scripts/run_perf.py seed --fleet fleet-10 --market market-100 --history daily
uv run python scripts/run_perf.py measure-api --fleet fleet-10 --mode smoke
```

The harness refuses to seed or flush unless `POSTGRES_DATABASE=cs2_perf` and
`REDIS_KEY_PREFIX=perf:` on 127.0.0.1. GitHub and AI substitutes listen on
`127.0.0.1:18080` (`uv run python scripts/run_perf.py stub`). SSH and A2S stay
unprobed: seeded servers have monitoring and auto-update off, and API
measurement uses FastAPI without background lifespan workers.

## Profiles

| Profile | Meaning |
| --- | --- |
| `fleet-10` / `fleet-100` / `fleet-500` | 10 / 100 / 500 servers and 1 / 10 / 30 concurrent sessions |
| `fleet-1000` / `fleet-1001` | Compatibility for the existing overview cap of 1000 |
| `market-100` / `market-1000` / `market-10000` | Marketplace listing counts |
| `empty` / `daily` / `max` | Operation history mix, including one server at the 7-day retention cap |
| `smoke` | 1 warmup + 2 measures × 1 round; API 5 s / 10 s × 1 |
| `baseline` | Browser 5 / 30 × 3; API 1 min / 5 min × 3; optional 60 min soak |

Users always include administrator, member, and two disjoint tenants.
Extra concurrent sessions reuse the administrator token so inbox scale stays
the worst case.

## Browser production baseline

CI `playwright.performance.config.ts` still runs the existing **development**
behavior suite. Comparable production numbers use a prior `npm run build` and:

```bash
cd frontend
PERF_PRODUCTION=1 PERF_WARMUP=5 PERF_MEASURE=30 PERF_ROUNDS=3 \
  npx playwright test --config=playwright.production-baseline.config.ts
```

Smoke defaults are `PERF_WARMUP=1 PERF_MEASURE=2 PERF_ROUNDS=1`. Reports land
in `frontend/test-results/perf-baseline/`. Catalog compact JSON bytes are not
HTML, RSC, or gzip transfer; the Playwright report records those separately.

On this tree the merged catalogs (main JSON plus `settings.monitor`) compact to
**142,964** bytes (en-US) and **139,750** bytes (zh-CN). Login/overview subsets
are about 2 KiB / 5 KiB. Those subset sizes are the Stage 3 opportunity; the
current client payload is still the full catalog because the root layout passes
`getMessages()`.

## Report format

JSON files use `"format": "upkk-isolated-perf"` and `"claimed_gains": false`.
See `reports/perf/schema.json`. Raw dumps go to `reports/perf/raw/` and are
not committed. Later comparison reports keep git SHAs, environment, data size,
raw summaries, and candidates that missed a gate.

## Stage 1 (algorithmic, p95 not yet measured)

Inbox GET/SSE/DELETE now load the authorized server id/name snapshot, close the
database session, then take one hub snapshot for the whole set. That snapshot
scans in-memory records once, fills missing indexes and records with chunked
`get_many()` (≤ 500 keys), and reads Redis event **tails** (`LRANGE -1 -1`,
bounded reverse window on corrupt tails) instead of full histories. Pruning a
stale index re-reads Redis and drops only IDs already known expired so a job
enqueued during the read is not overwritten. Sort, queue positions, seven-day
retention, SSE `inbox` events / 1s wait / keep-alive, and “clear failed”
including administrator AI import failures are unchanged. Isolated 100 / 500
server p95 has **not** been run; do not treat this as a claimed latency gain.

## Stage 2 (request lifecycle, production timings not yet measured)

Activity tray, AI import list, console panes, deploy SteamCMD tails, operation
journal/current fallbacks, and incomplete batch journals now share
`subscribeVisiblePoll`: one in-flight pull, skip overlapping ticks, abort when
the page is hidden or the subscriber leaves, and pull immediately when it
becomes visible. Console `serverId + pane kind` subscribers share one fetch.
The tray skips GET while an SSE snapshot is still inside the existing 8 / 20 s
window and keeps that polling fallback when the cross-tab SSE lock is elsewhere
or the snapshot is stale. AI import list polling still discovers new jobs;
selected-task detail uses the existing visible SSE helper instead of a second
EventSource. Public HTTP/SSE contracts are unchanged.

## Gates (not yet claimed)

These are the agreed acceptance checks. Stage 0 only records the protocol and
the current catalog/download facts:

- Inbox: one in-memory scan per snapshot; Redis batched; 100 / 500 p95 ≥ 20%
  or report the actuals
- In-flight ≤ 1; hidden pages pause; stale responses cannot win
- `/login` and `/overview` client message bytes −50%, plus real HTML/RSC size
- Bundle budgets unchanged (252 KiB / 150 KiB gzip)
- No p95 regression beyond `max(5%, 20 ms)`; CPU / RSS +5%; no monotonic leak
- Indexes only with EXPLAIN, 20% p95, write tolerance, and ≤ 5 s build

Application changes revert by commit. Added Alembic revisions stay; dropping
an index needs a forward revision. Push, deploy, and live restart are out of
scope for this harness.
