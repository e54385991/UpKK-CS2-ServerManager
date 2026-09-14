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
uv run python scripts/run_perf.py measure-api --fleet fleet-100 --mode smoke --route overview
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
is **18,521 / 18,291** bytes. Production Playwright records HTML / RSC / gzip
separately (see Stage 7).

## Stage 3 (locale trim and render coalesce)

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
limits, order, and dedup are unchanged. Catalog compact JSON is not HTML,
RSC, or on-the-wire gzip; Stage 7 records a production Playwright smoke of
those sizes. There is no pre-Stage-3 HTML capture at `e07dbd0`, so the −50%
gate is verified for **client catalog JSON**, not for document transfer.

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

`claimed_gains` remains **false**. Numbers below are one 5 s warmup + 10 s
measure round (API smoke), isolated `--route` smokes, a HEAD fleet-100 API
baseline (1 min warmup + 5 min × 3), or `PERF_WARMUP=5 PERF_MEASURE=30
PERF_ROUNDS=1` (browser) on loopback against isolated PostgreSQL 18.6 /
Redis 8.10.1 (`docker-compose.perf.yml`) and a production Next standalone +
mock API. Fleet-100 now has a matching 1 min / 5 min × 3 series at `e07dbd0`
and HEAD. A 60-minute mixed-load RSS soak on HEAD is recorded below.

### Local commits

| Stage | SHA | Change |
| --- | --- | --- |
| 0 | `e07dbd094786c4d2a0f4cef39c89bc4a69c5889e` | Isolated harness and report format |
| 1 | `7dc61fade1860511322f31b2e386a27d8f105fde` | Inbox batch snapshot and short DB transactions |
| 2 | `1b9c47f43634d6a6392d952f2be24716ed459f87` | Visible polling, cancel, shared pane fetches |
| 3 | `0796ee66d5fd36dc821287db5b04fd08d0bfd334` | Locale pick-messages and 50 ms stream coalesce |
| 4 | `507773400f6925339d953e29ba8f94fef9a4101e` | Overview column projection; no index revision |
| 5 | `686f863ddf6e89f6f97408ec0d6a4216aac08035` | One-scan cache prune; 8 KiB chunks kept |
| 7 | `79b349691cf28b600ba5ed232274bd5d37ec51ba` | Comparison notes and harness measurement fixes |
| 7b | `c1a64cd0e1172e529c708aa1dad25f4af0fc26d5` | Before/after smoke evidence; Playwright SSE/evaluate fix |
| 7c | `47786cc172e1df393015e3b523428c32f6c51f31` | Isolated `--route` smokes; 5/30 Playwright; RSS |
| 7d | `f3e2f55d8ae4b7a8742af702fcde3c68d5074aaa` | Live poll e2e; fleet-500 isolated actuals; 5/30×3 |
| 7e | this commit | Same-protocol 3-round before; 60-minute soak; `soak` CLI |

Before SHA is harness-only `e07dbd094786c4d2a0f4cef39c89bc4a69c5889e` (pre-inbox
batch). After SHA is `79b349691cf28b600ba5ed232274bd5d37ec51ba`. Same seed
manifest, isolated DB/Redis, loopback ASGI.

### Isolated API smoke (before vs after)

`scripts.perf.report.compare_summaries` on fleet-100 smoke:

| Gate | Result |
| --- | --- |
| Inbox p95 ≥ 20% | **pass** (1300.4 ms → 211.0 ms, **83.8%**) |
| Login catalog bytes −50% | **pass** (142,964 → 1,653) |
| Overview catalog bytes −50% | **pass** (142,964 → 18,521) |
| Mixed-load p95 regression `max(5%, 20 ms)` | **fail** for overview / market / servers (see coupling note) |

Admin inbox GET (authorized fleet). After: SQL stays 3 (auth + snapshot);
Redis round trips grow by batches, not by one-per-server.

| Fleet | users | before inbox p95 | after inbox p95 | before redis (probe) | after redis (probe) | after errors |
| --- | --- | --- | --- | --- | --- | --- |
| 100 | 10 | 1300.4 ms | 211.0 ms | 1501 | 4 | 0 |
| 500 | 30 | *mixed-load smoke did not finish* | 2256.3 ms | n/a | 14 | 0 |

The pre-batch inbox at 500 servers / 30 sessions exhausted the isolated Redis
client pool (`Too many connections` on per-event loads) and exited 1 with no
`api-fleet-500-before.json`. A single admin GET against the same 500-server
seed: **3258.5 ms** before vs **219.6 ms** after (measure-api probe; 2,324,627
bytes). Isolated `--route inbox` at 500 / 30 sessions (HEAD): **6426.7 ms**
p95, 0 HTTP errors, 108 samples. Redis logged `batch cache read failed
(keys=50)` under that stampede (telemetry MGET, not the inbox snapshot
pipeline). Mixed-load after was **2256 ms** because workers also spent time
on other routes. There is still no mixed-load or isolated 30-session p95 at
`e07dbd0` (Redis pool exhaustion).

**Fleet-500 isolated overview** (30 sessions, overview only), rerun after
Redis recovered from the inbox stampede:

| | before p95 | after p95 | regression |
| --- | --- | --- | --- |
| overview | 121.8 ms | 115.1 ms | **pass** (−5.5%) |

The first HEAD overview sample after the inbox isolate was 222.6 ms and is
discarded as contaminated.

**Mixed-load coupling.** Before, a slow inbox (~1.3 s at 100) starved the other
three routes so their p95 looked like 15–17 ms. After, inbox is fast and the
same 10 workers hammer overview / market / servers (91 / 108 / 102 ms). Isolated
`--route` smokes (same 10 sessions, one path only) remove that coupling:

| Route | before p95 | after p95 | regression `max(5%, 20 ms)` |
| --- | --- | --- | --- |
| overview | 43.4 ms | 24.7 ms | **pass** (−43%) |
| market | 42.2 ms | 37.2 ms | **pass** |
| servers | 31.7 ms | 38.1 ms | **pass** (+6.3 ms, limit 51.7 ms) |

RSS: mixed-load smoke 241.8 MiB → 236.4 MiB (−2.2%). Isolated overview 230.8 →
225.2 MiB. CPU seconds on the mixed-load smoke 16.27 → 15.98. All inside +5%.
`cpu_percent` is not sampled (null). 60-minute soak: see below.

### Fleet-100 API baseline (1 min / 5 min × 3, before vs after)

Median of three round p95 values, mixed four-route load, 0 errors. Before
(`e07dbd0`) ~46k requests/route; after (HEAD) ~38k. Inbox probe after: 146.7
ms, SQL 3, Redis 4.

| Route | before median p95 | after median p95 | gate |
| --- | --- | --- | --- |
| inbox | **1046.5** (1044.8 / 1046.5 / 1048.4) | **197.2** (188.2 / 199.6 / 197.2) | **pass (−81.2%)** |
| overview | 10.7 | 65.4 | mixed-load coupling (isolated `--route` pass) |
| market | 11.3 | 77.3 | mixed-load coupling |
| servers | 11.0 | 86.1 | mixed-load coupling |

RSS high-water: before 251.2 MiB, after 239.1 MiB. The inbox 20% gate holds
on this protocol. Overview/market/servers mixed-load p95 rise because a
1.0 s inbox no longer starves them; isolated fleet-100 `--route` smokes
stay inside `max(5%, 20 ms)`.

### Fleet-100 60-minute soak (HEAD)

`scripts/run_perf.py soak --fleet fleet-100 --mode baseline`: mixed four-route
load for 3600 s, RSS sampled each minute (62 points). Inbox probes stayed
HTTP 200, SQL 3; first 135.8 ms / Redis 4, last 24.7 ms / Redis 1.

| t | RSS |
| --- | ---: |
| 0 s (before load) | 197.0 MiB |
| 60 s | 226.6 MiB |
| 3600 s | 229.9 MiB |

From the first post-load sample to the end: **+1.45%**, inside +5%. The
naive t=0 comparison fails (`rss_pass` in the raw file is that naive
check); the harness now compares sample[1] to sample[-1]. RSS is not
monotonically climbing after minute 1 (it plateaus ~229–230 MiB).

### Production Playwright HTML / RSC / gzip (HEAD smoke)

Standalone Next 16.3.5 (`npm run build`) against `e2e/performance.mock.mjs`.
The server does **not** set `Content-Encoding`; `html_gzip_bytes` /
`rsc_gzip_bytes` are `gzipSync` of the uncompressed body (or the encoded
`Content-Length` when present). Full document navigations often embed RSC in
HTML, so `rsc_bytes` may be 0. `claimed_gains` stays false.

Uncached first-hit (0/1×1):

| Locale | Route | HTML | gzip(HTML) | RSC | gzip(RSC) | JS transfer | critical ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| en-US | `/login` | 27,518 | 5,961 | 0 | — | 179,617 | 158 |
| zh-CN | `/login` | 27,410 | 6,210 | 0 | — | 179,617 | 93 |
| en-US | `/overview` | 84,861 | 17,399 | 508 | 363 | 193,848 | 397 |
| zh-CN | `/overview` | 84,600 | 19,120 | 0 | — | 193,848 | 92 |

`PERF_WARMUP=5 PERF_MEASURE=30 PERF_ROUNDS=3`: HTML and gzip medians match the
first-hit table on every round. Critical-content p95 across the three rounds:

| Locale | Route | r1 / r2 / r3 p95 ms |
| --- | --- | --- |
| en-US | `/login` | 65 / 65 / 60 |
| zh-CN | `/login` | 79 / 83 / 78 |
| en-US | `/overview` | 86 / 90 / 125 |
| zh-CN | `/overview` | 97 / 110 / 111 |

JS transfer p50 is **0** on repeat visits because the browser cache reports
`transferSize` 0. Uncached first-hit JS remains 179,617 / 193,848 bytes.

There is no matching HTML capture at `e07dbd0`. The −50% locale gate remains
the catalog JSON subsets above, not these documents.

The production baseline runner must not call `response.body()` on the
activity-tray EventSource (`text/event-stream` never ends) and must `await`
`page.evaluate` before closing the page.

### Polling (unit + live browser)

`frontend/src/shared/lib/visible-poll.test.ts`: in-flight ≤ 1, hidden pause,
ignore-after-stop, shared poll until the last listener leaves.

`e2e/performance.spec.ts` “activity-tray inbox poll stays single-flight and
pauses while hidden”: on `/overview`, a gated `/api/v1/operations/inbox` GET
stays at one in-flight request through a second `plugin-ai-import-submitted`
refresh; `document.hidden` stops a third; becoming visible starts exactly one
more. Passed under `playwright.performance.config.ts`.

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
- Production Playwright must skip `response.body()` on `text/event-stream`
  (activity-tray SSE) and `await` CDP evaluate before closing the page.
- `measure-api --route overview` (repeatable) isolates one path; default is
  the mixed inbox/overview/market/servers load.
- `soak --mode baseline` runs 3600 s mixed load and samples RSS each minute.
  Compare the sample after the first minute to the last; t=0 is pre-load.

## Gates (not yet claimed)

These are the agreed acceptance checks. Stage 0 only records the protocol and
the current catalog/download facts:

- Inbox: one in-memory scan per snapshot; Redis batched; 100 / 500 p95 ≥ 20%
  or report the actuals — **100 smoke pass (83.8%)**; **500 mixed-load before
  collapsed**; mixed-load after 2256 ms; isolated 30-session inbox after
  **6427 ms** (0 errors); single GET 3258 → 220 ms. Fleet-100 **same-protocol**
  1 min / 5 min × 3 inbox median p95 **1046.5 → 197.2 ms (−81.2%)**.
- In-flight ≤ 1; hidden pages pause; stale responses cannot win — **unit tests
  and live activity-tray Playwright pass**
- `/login` and `/overview` client message bytes −50%, plus real HTML/RSC size —
  **catalog JSON pass**; HTML/RSC 5/30×3 table above (no pre-trim HTML baseline)
- Bundle budgets unchanged (253 KiB route / 150 KiB chunk gzip) — **`npm run
  check:bundle` passed for 40 pages**
- No p95 regression beyond `max(5%, 20 ms)`; CPU / RSS +5%; no monotonic leak —
  **fleet-100 isolated `--route` smokes pass**; fleet-500 isolated overview
  121.8 → 115.1 ms. Mixed-load overview/market/servers still look worse because
  a fast inbox no longer starves them. RSS/CPU smoke and the HEAD baseline RSS
  stay inside +5%. Isolated 500-inbox RSS 397.7 MiB during the 30-wide stampede
  (process peak, not a leak series). 60-minute soak: **+1.45% RSS after warmup**,
  plateau ~230 MiB, not a monotonic leak
- Indexes only with EXPLAIN, 20% p95, write tolerance, and ≤ 5 s build

Application changes revert by commit. Added Alembic revisions stay; dropping
an index needs a forward revision. Push, deploy, and live restart are out of
scope for this harness.
