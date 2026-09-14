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
**142,964** bytes (en-US) and **139,750** bytes (zh-CN). After Stage 3, the
login client subset is **1,653 / 1,582** bytes and the overview chrome subset
is **18,521 / 18,291** bytes. HTML, RSC, and gzip transfer still belong in the
production Playwright report.

## Stage 3 (locale trim and render coalesce; HTML/RSC gzip not yet measured)

Root `NextIntlClientProvider` now receives only `feedback`. Console chrome,
auth pages, and domain layouts pass explicit namespaces through
`ClientMessages` / `pickMessages` (nested paths such as `plugins.aiImport`).
Server workspace pages share one workspace dictionary on the existing
`servers/[id]` layout so extra layout client entries do not blow the files
route gzip budget. Source JSON files stay intact; `getRequestConfig` still
loads the full server dictionary including `settings.monitor`. The files
move dialog is lazy-loaded with the other file dialogs. AI token deltas and
operation-log SSE lines coalesce to at most one display update per 50 ms;
complete, error, and approval events flush immediately. Existing event
limits, order, and dedup are unchanged. Isolated production HTML/RSC/gzip
sizes have **not** been re-measured in a browser; catalog compact JSON is
not that transfer.

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

## Stage 4 (overview column projection; indexes not submitted)

`GET /api/v1/overview/summary` now loads only `status` and `max_players` for
the current user's servers, still capped at 1000 unordered rows, and counts
running / attention / capacity in `services.overview.summary`. Administrators
still only count their own servers. The request session is closed before the
SSH pool Redis read. Marketplace list and audit list keep an exact `COUNT(*)`
plus a page of ORM rows: empty pages must still return the true total, and
list DTOs include `description_i18n` / `ai_metadata` (market) and `details`
(audit). Inbox SQL remains the Stage 1 id/name snapshot.

Two marketplace B-tree candidates are recorded in
`scripts/perf/index_eval.py` and can be EXPLAINed with:

```bash
uv run python scripts/run_perf.py explain-indexes
```

They are **not** in Alembic. Inclusion still requires existing indexes not
covering the query, a 20% three-round p95 median, write tolerance, and ≤ 5 s
build on the largest isolated set. Startup migration stays transactional, so
`CREATE INDEX CONCURRENTLY` cannot be used. Isolated EXPLAIN on 1,000 and
10,000 fixture rows is recorded in Stage 7; candidates still stay out
because HTTP three-round p95 and write tolerance were not met.

## Stage 5 (cache prune one scan; 64 KiB chunk not adopted)

`plugin_download_cache.prune` now lists the cache directory once, stats each
name, and reuses that list for stale `.part` cleanup, age expiry, and LRU
capacity. Store-then-prune timing is unchanged. Isolated 4 MiB
await-per-chunk copies (matching `aiter_bytes` + `anyio` writes) measured:

| chunk | writes | throughput_bps | cancel_ms | tracemalloc peak |
| --- | --- | --- | --- | --- |
| 8 KiB | 512 | 83,933,922 | 0.138 | 32,049 |
| 64 KiB | 64 | 670,882,974 | 0.116 | 72,809 |
| 256 KiB | 16 | 2,310,169,272 | 0.158 | 269,033 |

64 KiB throughput is well above +10% and cancel / loop p95 stay inside
`max(5%, 20 ms)`. Traced peak memory rose +127%, so the candidate **misses
the memory gate** and production `DOWNLOAD_CHUNK_SIZE` stays **8192**. The
shared httpx client is unchanged.

## Stage 7 (comparison and maintenance)

`claimed_gains` remains **false**. Smoke API numbers below are one 5 s warmup +
10 s measure round on loopback ASGI against isolated PostgreSQL 18.6 /
Redis 8.10.1 (`docker-compose.perf.yml`). They are not the agreed 1 min / 5
min × 3 API protocol, not a 60-minute soak, and not a production Playwright
5 / 30 × 3 browser loop. There is no pre-change SHA measurement, so the
inbox 20% p95 gate is **unverified**.

### Local commits

| Stage | SHA | Change |
| --- | --- | --- |
| 0 | `e07dbd094786c4d2a0f4cef39c89bc4a69c5889e` | Isolated harness and report format |
| 1 | `7dc61fade1860511322f31b2e386a27d8f105fde` | Inbox batch snapshot and short DB transactions |
| 2 | `1b9c47f43634d6a6392d952f2be24716ed459f87` | Visible polling, cancel, shared pane fetches |
| 3 | `0796ee66d5fd36dc821287db5b04fd08d0bfd334` | Locale pick-messages and 50 ms stream coalesce |
| 4 | `507773400f6925339d953e29ba8f94fef9a4101e` | Overview column projection; no index revision |
| 5 | `686f863ddf6e89f6f97408ec0d6a4216aac08035` | One-scan cache prune; 8 KiB chunks kept |
| 7 | this commit | Comparison notes and harness measurement fixes |

### Isolated API smoke (HEAD after Stage 5)

Admin inbox GET (authorized fleet). SQL stays 3 (auth + snapshot). Redis
round trips grow by batches, not by one-per-server:

| Fleet | online users | inbox p95 ms | inbox redis ops (probe) | inbox SQL (probe) | errors |
| --- | --- | --- | --- | --- | --- |
| 10 | 1 | 10.2 | 2 | 3 | 0 |
| 100 | 10 | 230.3 | 4 | 3 | 0 |
| 500 | 30 | 2335.3 | 14 | 3 | 0 |

Overview summary smoke p95: 3.7 / 105.5 / 730.9 ms. Market list (1,000
rows at the 100- and 500-server seeds): 4.2 / 85.2 / 604.8 ms. Catalog
client compact JSON remains 1,653 / 1,582 bytes for login and 18,521 /
18,291 for overview chrome (en-US / zh-CN), more than 50% below the full
catalog. HTML / RSC / gzip transfer is still unmeasured in a browser.

### Rejected or unsubmitted candidates

- **Marketplace B-trees.** Existing indexes (`pk`, `github_url`, `title`)
  do not cover recommended/newest sorts. On 10,000 fixture rows a
  transactional `CREATE INDEX` built in 9 ms and 8 ms and rolled back.
  `EXPLAIN (ANALYZE)` list time dropped 1.107 ms → 0.027 ms and 1.769 ms
  → 0.032 ms; count only 1.063 ms → 0.963 ms (~9%). No three-round HTTP
  p95 series and no write-tolerance run, so **no Alembic revision**.
- **64 KiB download chunks.** Throughput gate passed; traced peak memory
  did not. Production chunk size stays 8 KiB.
- **`cacheComponents` / `partialPrefetching`.** Remain off.

### Rollback

Revert application commits newest-first (`686f863`, `5077734`, `0796ee6`,
`1b9c47f`, `7dc61fa`, then the harness if desired). This round added **no**
Alembic revision. If a later index revision exists, keep it and add a
forward migration to drop the index; do not run an old image that does not
recognize that revision.

Push, deploy, and live restart stay out of default scope.

### Maintenance

- Apply isolated env (`scripts/run_perf.py`) before importing
  `modules.config` or `modules.database`.
- `upgrade_database` requires `lock_timeout_seconds`; the seed harness
  passes `DB_MIGRATION_LOCK_TIMEOUT_SECONDS`.
- Portable plugin-framework strings in PostgreSQL are enum **names**
  (`COUNTERSTRIKESHARP`), not values (`counterstrikesharp`).
- Hub code must not module-import `services.operations.*`; snapshot
  helpers must not module-import the hub.
- Nested `NextIntlClientProvider` replaces messages; keep `feedback` at the
  root and do not add extra `servers/[id]/*` layouts for i18n.
- Cache prune is one `iterdir`; do not restore a second walk.
- `reports/perf/raw/*.json` stays uncommitted.

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
