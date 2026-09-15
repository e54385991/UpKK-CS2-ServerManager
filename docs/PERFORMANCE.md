# Isolated performance measurement

This is the Stage 0 harness for the non-destructive full-stack performance
work. The numbers it produces are **measurements**, not accepted production
gains. Timing comparisons stay on a fixed isolated host; default CI keeps
deterministic call-count and protocol tests only.

## Round `21197fe` (this comparison window)

This round starts at `21197fec25b785db2200e9feb6fdb245559da773`. Historical
Stage 7 numbers (including isolated inbox p95 ≈ 6427 ms at 500 / 30) stay in
the archive below. They are **not** this round's starting measurement.

Fixed protocol for every before/after pair in this round:

| Item | Value |
| --- | --- |
| Starting SHA | `21197fec25b785db2200e9feb6fdb245559da773` |
| Locks | current `uv.lock` and `frontend/package-lock.json` |
| Stack | `docker-compose.perf.yml` PostgreSQL 18.6 + Redis 8.10.1 on loopback |
| Database / prefix | `cs2_perf` / `perf:` |
| Arrival | 80% of the starting version's error-free throughput; candidates reuse those targets |
| API baseline | 60 s warmup, 300 s measure, 3 rounds; median of round p95 |
| Realtime | 30 inbox SSE sessions plus progress / complete / fail / clear |
| Browser | production build, 5 warmup / 30 measure / 3 rounds; en-US + zh-CN; 390 and 1440 |
| Segments | SQL count/ms, pool checked-out, Redis ops/ms/bytes, lock wait, snapshot, DTO, JSON, loop lag |
| `claimed_gains` | false until a candidate beats the same-protocol starting run |

Commands added for this round:

```bash
uv run python scripts/run_perf.py fingerprint
uv run python scripts/run_perf.py measure-api --fleet fleet-100 --mode smoke --paced
uv run python scripts/run_perf.py measure-api --fleet fleet-100 --mode baseline --paced --arrival-from reports/perf/raw/api-fleet-100-baseline.json
uv run python scripts/run_perf.py measure-api --fleet fleet-500 --mode baseline --paced --route market --with-index-candidates --arrival-from reports/perf/raw/api-fleet-500-smoke-market-21197fe.json
uv run python scripts/run_perf.py measure-realtime --fleet fleet-500 --mode smoke --sessions 30
```

`--paced` discovers error-free closed-loop rps on the starting tree, stores
`arrival.targets`, and then emits each route independently. Missed and late
slots are recorded so a slow inbox cannot reduce overview / market / servers
volume. Snapshot / DTO / JSON segment fields stay `null` until those timers
exist in the hub; SQL, Redis, pool checkout, and loop lag are collected now.

Compatibility cases (`fleet-1000` / `fleet-1001`) only check the existing
overview 1000-row cap. Primary inbox work stays on 10 / 100 / 500.

This machine's loopback `55432` / `56379` may already be bound by another
compose project. Set `UPKK_PERF_POSTGRES_PORT` / `UPKK_PERF_REDIS_PORT`
(this host: `55442` / `56389`) so fingerprint and seed talk to
`docker-compose.perf.yml`. Fingerprint records `postgres_listening` /
`postgres_isolated` and `redis_listening` / `redis_isolated`. A listening
port is not enough: `postgres_isolated` is true only when the isolated user
can open `cs2_perf`. Do not seed unless that flag is true and
`REDIS_KEY_PREFIX=perf:`.

`cacheComponents` and `partialPrefetching` remain off. This harness does not
change that gate.

## Round `21197fe` delivery

Implementation batches on this tree, newest last. None of them change HTTP,
SSE, or WebSocket contracts. `claimed_gains` stays **false** until a
same-protocol 60 s / 300 s × 3 series at `21197fe` beats HEAD.

This host's default loopback `55432` / `56379` stay with another compose
project (`xproj-public-perf`). The isolated stack for this round is
`docker-compose.perf.yml` on **`55442` / `56389`**
(`UPKK_PERF_POSTGRES_PORT` / `UPKK_PERF_REDIS_PORT`). Fingerprint
`postgres_isolated` and `redis_isolated` are **true**. Images are
PostgreSQL 18.6 and Redis 8.10.1. Database `cs2_perf`, prefix `perf:`.
Do not stop the other project.

Seeded set: `fleet-500` / `market-10000` / `history max`
(`reports/perf/raw/seed-manifest-fleet-500.json`).

Paced inbox **smoke** (10 s, 1 round, 5 samples, 0 errors) is **not** a
20% gate. Arrival discovered 0.633 rps on `21197fe`; target 0.506 rps is
reused by HEAD.

Paced market **smoke** (10 s, 0 errors) discovered 523 rps and targets
418 rps. Actual emit is lower because each GET is ~6 ms, so the open-loop
schedule misses slots. That cadence is reused:

| Tree | market p95 | samples |
| --- | ---: | ---: |
| starting | 6.14 ms | 1788 |
| HEAD | 8.45 ms | 1478 |

Same-protocol market **baseline** (60 s + 300 s × 3, 0 errors):

| Tree | median p95 | round p95 | samples |
| --- | ---: | --- | ---: |
| starting | **7.54 ms** | 7.54 / 6.76 / 8.77 | 145022 |
| HEAD | **7.10 ms** | 7.03 / 7.10 / 7.82 | 145812 |

Market 20% gate: **fail** (+5.8%). Still inside `max(5%, 20 ms)`.
Indexed HTTP (same arrival, then dropped): **6.01 ms** median
(6.06 / 6.01 / 4.55), **15.4%** vs HEAD 7.10 ms. Below 20%, so **no
Alembic revision**.

`measure-api --with-index-candidates` creates the two btree candidates,
measures, then `DROP INDEX` in `finally`. It is not an Alembic revision.
After that pair exists, decide without touching the database:

```bash
uv run python scripts/run_perf.py explain-indexes \
  --eval-from reports/perf/raw/explain-indexes-fleet-500.json \
  --http-before reports/perf/raw/api-fleet-500-baseline-market-head.json \
  --http-after reports/perf/raw/api-fleet-500-baseline-market-head-indexed.json
```

| Tree | git SHA | inbox p95 | late slots |
| --- | --- | ---: | ---: |
| starting | `21197fe` | 2174 ms | 1 |
| HEAD at measure | `1156678` | 2140 ms | 2 |

Same-protocol paced inbox **baseline** (60 s warmup + 300 s × 3, arrival
0.506 rps reused from the starting smoke, 0 HTTP errors):

| Tree | git SHA | median p95 | round p95 | samples | late (last round) |
| --- | --- | ---: | --- | ---: | ---: |
| starting | `21197fe` | **2156 ms** | 2748 / 2156 / 2156 | 453 | 136 |
| HEAD | `d16e555` | **2210 ms** | 2299 / 2209 / 2210 | 453 | 140 |

Inbox 20% gate: **fail** (−2.5%). The change is inside `max(5%, 20 ms)`
(limit 2264 ms), so it is not a mixed-load-style regression either.
`claimed_gains` stays **false**. Machine-readable copy:
`reports/perf/round-21197fe-comparison.json`. Historical Stage 7 timings
stay in the archive and are not this round's before/after.

Paced overview **smoke** (10 s, 0 errors) discovered 511.406 rps and
targets 409.125 rps. Open-loop emit is ~323 rps, so slots are missed.
That cadence is reused:

| Tree | overview p95 | samples |
| --- | ---: | ---: |
| starting | 3.88 ms | 3222 |
| HEAD | 3.84 ms | 3233 |

Same-protocol overview **baseline** (60 s + 300 s × 3, 0 errors, arrival
reused):

| Tree | median p95 | round p95 | samples |
| --- | ---: | --- | ---: |
| starting | **3.55 ms** | 3.59 / 3.44 / 3.55 | 306253 |
| HEAD | **3.72 ms** | 3.65 / 3.72 / 3.80 | 298967 |

Overview 20% gate: **fail** (−4.6%). Still inside `max(5%, 20 ms)`
(limit 3.73 ms). Smoke is not a 20% gate.

Paced servers **smoke** (10 s, 0 errors) discovered 289.274 rps and
targets 231.419 rps. Open-loop emit is ~196–201 rps, so slots are
missed. That cadence is reused:

| Tree | servers p95 | samples |
| --- | ---: | ---: |
| starting | 5.95 ms | 1955 |
| HEAD | 5.90 ms | 2011 |

Same-protocol servers **baseline** (60 s + 300 s × 3, 0 errors, arrival
reused):

| Tree | median p95 | round p95 | samples |
| --- | ---: | --- | ---: |
| starting | **5.81 ms** | 5.89 / 5.81 / 5.75 | 184160 |
| HEAD | **5.79 ms** | 5.79 / 5.79 / 5.79 | 184362 |

Servers 20% gate: **fail** (+0.2%). Still inside `max(5%, 20 ms)`.
Smoke is not a 20% gate.

Mixed `--paced` reuses the four single-route arrival targets so a slow
inbox cannot reduce the others' scheduled volume. Smoke (10 s, 0
errors) is **not** a 20% gate:

| Tree | inbox | overview | market | servers |
| --- | ---: | ---: | ---: | ---: |
| starting | 2604 ms | 17.8 ms | 141.6 ms | 83.5 ms |
| HEAD | 2809 ms | 22.4 ms | 148.4 ms | 152.2 ms |

Same-protocol mixed **baseline** (60 s + 300 s × 3, 0 errors):

| Route | starting median | HEAD median | Δ | envelope |
| --- | ---: | ---: | ---: | --- |
| inbox | **2603 ms** | **2750 ms** | −5.7% | outside `max(5%, 20 ms)` |
| overview | **20.8 ms** | **25.5 ms** | −22.3% | inside 20 ms floor |
| market | **86.2 ms** | **153 ms** | −77.4% | outside |
| servers | **107 ms** | **154 ms** | −44.2% | outside |

Starting rounds: inbox 2602 / 2612 / 2603; overview 20.7 / 22.2 / 20.8;
market 84.7 / 134.9 / 86.2; servers 99.6 / 106.7 / 140.9.
HEAD rounds: inbox 2714 / 2808 / 2750; overview 94.9 / 24.7 / 25.5;
market 154.1 / 152.9 / 152.7; servers 156.4 / 153.9 / 153.7.
20% gate: **fail** on every mixed route. `claimed_gains` stays **false**.
Concurrent open-loop at isolated rates misses most overview / market /
servers slots.

30-session realtime **smoke** (15 s, 0 HTTP errors) opened 30 inbox
SSE sessions on both trees. Neither delivered events (`events: {}`,
first-inbox p95 0). Production `REDIS_POOL_SIZE=10` is exhausted by 30
concurrent inbox snapshots; the pool size was **not** raised. The
harness now ends the window instead of hanging (`d4bb00a`). Same-protocol
**60 s baseline** on both trees is the same: 30 sessions, 0 HTTP errors,
0 events. `claimed_gains` stays **false**.

| Batch | SHA | Change |
| --- | --- | --- |
| 0 | `025bd71` | Starting protocol, arrival pacing, segments, realtime CLI, fingerprint |
| 1a | `216dcb7` | Visible-poll request identity; stale `finally` cannot clear a newer pull |
| 1b | `84cd598` | Session cookie helpers in shared; ESLint blocks shared → modules/app |
| 2a | `24c478c` | Inbox snapshot from the visible set; history prune Redis I/O off the hub lock |
| 2b | `3fe3878` | Inbox SSE reuses JSON only after a full domain snapshot compare |
| 3 | `bac25c5` | Plugin preflight short transaction; batch dependency reads; install routes split |
| 4a | `87e3b43` | Activity tray subscription / list reuse / commands / log pane |
| 4b | `d92a269` | Market install form, preflight, confirms, and live log split |
| 4c | `bf44716` | Assistant SSE + 2 s poll request identity; memoized completed messages |
| 4d | `e33a602` | Files directory / selection / editor / upload store / queue split |
| 5 | `66397d8` | Index coverage by column prefix; HTTP-shaped EXPLAIN SQL; no Alembic revision |
| ports | `1156678` | Isolated stack can move off 55432 / 56379 |
| contracts | `f533781` | Source contracts follow the splits; files route stays under gzip budget |
| format | `ebdfcaa` | Ruff format so pre-commit stops rewriting the tree |
| index probe | `17c0706` | Transactional write + CREATE INDEX (rollback) before inclusion |
| 6 | this document | Comparison, remaining limits, and rollback for this round |

### Index decision

The two marketplace btree candidates remain **out**. Alembic `0001` already
has `pk_market_plugins (id)`, `ix_market_plugins_github_url (github_url)`,
and `ix_market_plugins_title (title)`. Those prefixes do not cover
`(framework, is_recommended, install_count, created_at, id)` or
`(framework, created_at, id)`. Isolated `explain-indexes` on the 10,000-row
set (ports 55442 / 56389) captured:

| Gate | Result |
| --- | --- |
| Coverage | existing prefixes do not cover either candidate |
| EXPLAIN recommended | 1.808 ms Seq/heapsort → 0.043 ms Index Scan |
| Build | 13 ms and 6 ms transactional `CREATE INDEX`, then rollback |
| Writes | 0.72 ms → 0.46 ms p95, inside `max(5%, 20 ms)` |
| HTTP three-round p95 | 7.101 ms → 6.010 ms (**15.4%**, gate 20%) |

Inclusion **fails closed**. Measurement indexes were dropped; `pg_indexes`
is back to pk / github_url / title. See
`reports/perf/round-21197fe-indexes.json`.

### Correctness that landed without a speed claim

- Hidden → visible polling: only the current request id may clear inflight,
  publish, or re-arm; hide / resume / stop invalidate older pulls.
- Shared HTTP clients read cookie names from `shared/auth`, not `modules/auth`.
- Inbox assemble: one authorized-server scan, messages only for the visible
  set, event tails only for those operation ids, prune merge after Redis.
- Inbox SSE: equality includes messages, commands, queue position, results,
  administrator AI import rows, and history expiry; encode still runs the
  presenter and `OperationInboxView` when the snapshot changes.
- Plugin preflight: owned short read → close → SSH → short rule read. The
  compatibility `build_plugin_install_plan(db, ...)` still uses the caller
  session and does not commit it. Submit still re-preflights before 202.
- Frontend splits keep confirm dialogs, 8 / 20 s tray polls, assistant 2 s
  poll (no hide-to-pause), lazy file editor / terminal, and upload cancel.

Fleet-100 was reseeded after soak (`market-1000` / `history max`,
manifest `reports/perf/raw/seed-manifest-fleet-100.json`). Isolated
single-route **smoke** (10 s × 1, 0 errors, arrival from `21197fe`)
is **not** a 20% gate:

| Route | target rps | starting p95 | HEAD p95 | samples |
| --- | ---: | ---: | ---: | ---: |
| inbox | 8.937 | 405 ms | 407 ms | 38 / 38 |
| overview | 593.244 | 2.84 ms | 3.38 ms | 3947 / 3629 |
| market | 388.573 | 4.85 ms | 4.96 ms | 2447 / 2421 |
| servers | 318.414 | 5.43 ms | 5.66 ms | 2311 / 2312 |

HEAD SHA at those smokes: `1fb6942`. Inbox and servers miss open-loop
slots. Same-protocol inbox **baseline** (60 s + 300 s × 3, 0 errors,
8.937 rps reused):

| Tree | median p95 | round p95 | samples |
| --- | ---: | --- | ---: |
| starting | **406 ms** | 404 / 406 / 407 | 3315 |
| HEAD `7c7cb2d` | **413 ms** | 411 / 413 / 414 | 3246 |

Inbox 20% gate: **fail** (−1.8%). Still inside `max(5%, 20 ms)`
(limit 426 ms).

Same-protocol overview **baseline** (60 s + 300 s × 3, 0 errors,
593.244 rps reused):

| Tree | median p95 | round p95 | samples |
| --- | ---: | --- | ---: |
| starting | **3.35 ms** | 3.34 / 3.35 / 3.35 | 330547 |
| HEAD `3750245` | **3.37 ms** | 3.36 / 3.37 / 3.37 | 328808 |

Overview 20% gate: **fail** (−0.6%). Still inside `max(5%, 20 ms)`
(20 ms floor).

Same-protocol market **baseline** (60 s + 300 s × 3, 0 errors,
388.573 rps reused):

| Tree | median p95 | round p95 | samples |
| --- | ---: | --- | ---: |
| starting | **4.78 ms** | 4.78 / 4.78 / 4.79 | 225569 |
| HEAD `93b0d16` | **4.81 ms** | 4.81 / 4.87 / 4.81 | 223819 |

Market 20% gate: **fail** (−0.6%). Still inside `max(5%, 20 ms)`.

Same-protocol servers **baseline** (60 s + 300 s × 3, 0 errors,
318.414 rps reused):

| Tree | median p95 | round p95 | samples |
| --- | ---: | --- | ---: |
| starting | **5.46 ms** | 5.46 / 5.46 / 5.45 | 209442 |
| HEAD `0f906f2` | **5.52 ms** | 5.53 / 5.52 / 5.41 | 208152 |

Servers 20% gate: **fail** (−1.1%). Still inside `max(5%, 20 ms)`.

Mixed `--paced` reuses the four single-route arrival targets. Smoke
(10 s, 0 errors) is **not** a 20% gate:

| Tree | inbox | overview | market | servers |
| --- | ---: | ---: | ---: | ---: |
| starting | 534 ms | 136 ms | 156 ms | 139 ms |
| HEAD `dca867e` | 539 ms | 137 ms | 140 ms | 139 ms |

Same-protocol mixed **baseline** (60 s + 300 s × 3, 0 errors):

| Route | starting median | HEAD median | Δ | envelope |
| --- | ---: | ---: | ---: | --- |
| inbox | **506 ms** | **515 ms** | −1.9% | inside |
| overview | **141 ms** | **141 ms** | +0.2% | inside |
| market | **145 ms** | **145 ms** | −0.3% | inside |
| servers | **144 ms** | **143 ms** | +0.2% | inside |

Starting rounds: inbox 510 / 504 / 506; overview 140.0 / 140.9 / 141.0;
market 144.2 / 145.1 / 145.6; servers 142.5 / 143.6 / 143.9.
HEAD `2ba62c9` rounds: inbox 524 / 513 / 515; overview 140.7 / 140.9 /
140.3; market 146.0 / 145.5 / 144.9; servers 143.3 / 143.5 / 143.1.
20% gate: **fail** on every mixed route. All four stay inside
`max(5%, 20 ms)`.

10-session realtime **smoke** (15 s) and **baseline** (60 s) on both
trees: 0 HTTP errors, 0 events, first-inbox p95 0. Production
`REDIS_POOL_SIZE=10` is still exhausted; the pool size was **not**
raised.

Fleet-10 was reseeded after that series (`market-100` / `history max`,
manifest `reports/perf/raw/seed-manifest-fleet-10.json`). Isolated
single-route **smoke** (10 s × 1, 0 errors, arrival from `21197fe`)
is **not** a 20% gate:

| Route | target rps | starting p95 | HEAD p95 | samples |
| --- | ---: | ---: | ---: | ---: |
| inbox | 22.078 | 35.3 ms | 37.4 ms | 220 / 220 |
| overview | 299.674 | 3.28 ms | 3.30 ms | 2996 / 2996 |
| market | 207.342 | 4.62 ms | 4.51 ms | 2073 / 2073 |
| servers | 266.236 | 3.76 ms | 3.80 ms | 2662 / 2662 |

HEAD SHA at those smokes: `f28c164`. Same-protocol inbox **baseline**
(60 s + 300 s × 3, 0 errors, 22.078 rps reused):

| Tree | median p95 | round p95 | samples |
| --- | ---: | --- | ---: |
| starting | **36.7 ms** | 36.6 / 37.0 / 36.7 | 19869 |
| HEAD `181d4cd` | **36.9 ms** | 36.8 / 37.2 / 36.9 | 19869 |

Inbox 20% gate: **fail** (−0.5%). Still inside `max(5%, 20 ms)`.

Same-protocol overview **baseline** (60 s + 300 s × 3, 0 errors,
299.674 rps reused):

| Tree | median p95 | round p95 | samples |
| --- | ---: | --- | ---: |
| starting | **3.30 ms** | 3.31 / 3.30 / 3.30 | 269702 |
| HEAD `1eb0fa8` | **3.34 ms** | 3.34 / 3.34 / 3.34 | 269691 |

Overview 20% gate: **fail** (−1.1%). Still inside `max(5%, 20 ms)`.

Same-protocol market **baseline** (60 s + 300 s × 3, 0 errors,
207.342 rps reused):

| Tree | median p95 | round p95 | samples |
| --- | ---: | --- | ---: |
| starting | **4.59 ms** | 4.58 / 4.59 / 4.59 | 186606 |
| HEAD `202d0be` | **4.61 ms** | 4.62 / 4.61 / 4.60 | 186606 |

Market 20% gate: **fail** (−0.5%). Still inside `max(5%, 20 ms)`.

Same-protocol servers **baseline** (60 s + 300 s × 3, 0 errors,
266.236 rps reused):

| Tree | median p95 | round p95 | samples |
| --- | ---: | --- | ---: |
| starting | **3.81 ms** | 3.81 / 3.81 / 3.81 | 239610 |
| HEAD `c6cabbd` | **3.82 ms** | 3.81 / 3.82 / 3.83 | 239610 |

Servers 20% gate: **fail** (−0.2%). Still inside `max(5%, 20 ms)`.

Mixed `--paced` reuses the four single-route arrival targets. Smoke
(10 s, 0 errors) is **not** a 20% gate:

| Tree | inbox | overview | market | servers |
| --- | ---: | ---: | ---: | ---: |
| starting | 109 ms | 23.0 ms | 24.6 ms | 22.6 ms |
| HEAD `46eacd7` | 74.5 ms | 21.8 ms | 24.6 ms | 21.8 ms |

Same-protocol mixed **baseline** (60 s + 300 s × 3, 0 errors):

| Route | starting median | HEAD median | Δ | envelope |
| --- | ---: | ---: | ---: | --- |
| inbox | **54.8 ms** | **55.8 ms** | −1.8% | inside |
| overview | **22.1 ms** | **21.8 ms** | +1.4% | inside |
| market | **25.3 ms** | **24.6 ms** | +2.7% | inside |
| servers | **23.0 ms** | **22.4 ms** | +2.4% | inside |

Starting rounds: inbox 54.1 / 55.2 / 54.8; overview 22.3 / 21.8 /
22.1; market 25.3 / 25.3 / 25.3; servers 22.9 / 23.0 / 23.0.
HEAD `d04a5c9` rounds: inbox 55.1 / 55.8 / 56.0; overview 21.8 / 21.8 /
21.8; market 24.6 / 24.7 / 24.6; servers 22.4 / 22.5 / 22.4.
20% gate: **fail** on every mixed route. All four stay inside
`max(5%, 20 ms)`.

1-session realtime **smoke** (15 s) and **baseline** (60 s) on both
trees: 0 HTTP errors, 0 events, first-inbox p95 0. The pool size was
**not** raised. `claimed_gains` stays **false**.

### Still open on this host

- Fleet-500 single-route and mixed 60 s / 300 s × 3 pairs are
  recorded above (all missed 20%; mixed market/servers/inbox also
  outside the 5%/20 ms envelope). Realtime 15 s smoke is in (0 SSE
  events at pool size 10). Fleet-500 soak RSS missed +5%.
- Production browser 5 / 30 × 3 at 390 / 1440 passed (29 tests,
  6.3 min). Interactive pass opened the tray, install form,
  assistant conversation, and files workspace on the mock
  production stack. `check_baseline` remains.
- Production browser 5 / 30 × 3
- Isolated `fleet-1000` / `fleet-1001` seed is recorded (1000 / 1001
  rows). Admin ownership is 898 / 899, so overview summary returns
  898 / 899 and does not hit the SQL LIMIT 1000. Unit tests remain
  the cap proof.
- Marketplace indexes remain out (HTTP +15.4%, below 20%)
- Full `uv run python scripts/check_baseline.py` after `f533781` / `ebdfcaa`
  (the 03:46 contract/bundle failures now pass in isolation: token_usage,
  session cookie suffix, tray failed-import/history, files gzip)
- Interactive browser pass of tray, install, assistant, and files
  (production runner now covers those surfaces at 390 / 1440; numbers
  not captured yet)

Always export `UPKK_PERF_POSTGRES_PORT=55442 UPKK_PERF_REDIS_PORT=56389`
before `scripts/run_perf.py` on this machine. Do not seed unless
`postgres_isolated` is true and `REDIS_KEY_PREFIX=perf:`.

## Isolated stack

PostgreSQL 18.6 and Redis 8.10.1 use dedicated volumes and loopback ports.
They never reuse the live panel volumes.

```bash
export UPKK_PERF_POSTGRES_PORT=55442 UPKK_PERF_REDIS_PORT=56389
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

The production runner covers this round's browser matrix: login, overview,
servers, activity-tray (open the panel), plugins-install, assistant, and
files, in en-US / zh-CN at 390 and 1440. Subset with
`PERF_BROWSER_ROUTES=login,overview` and `PERF_WIDTHS=1440`. Smoke defaults
are `PERF_WARMUP=1 PERF_MEASURE=2 PERF_ROUNDS=1`. Reports land in
`frontend/test-results/perf-baseline/`. Catalog compact JSON bytes are not
HTML, RSC, or gzip transfer; the Playwright report records those separately.

This-round HEAD production run (`d7c832b`, 29 passed, 6.3 min). Median
of the three round p95 `critical_content_ms` values (not a 20% gate):

| Surface | en-US 390 | en-US 1440 | zh-CN 390 | zh-CN 1440 |
| --- | ---: | ---: | ---: | ---: |
| login | 63 | 65 | 61 | 61 |
| overview | 78 | 76 | 81 | 82 |
| servers | 91 | 83 | 89 | 92 |
| activity-tray | 120 | 126 | 123 | 128 |
| plugins-install | 80 | 87 | 93 | 92 |
| assistant | 73 | 77 | 79 | 82 |
| files | 89 | 90 | 96 | 101 |

`claimed_gains` stays **false**. Activity-tray visits log
`ResponseAborted` when the inbox SSE is closed; that is expected.

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

## Stage 1 (inbox batch snapshot)

Inbox GET/SSE/DELETE now load the authorized server id/name snapshot, close the
database session, then take one hub snapshot for the whole set. That snapshot
scans in-memory records once, fills missing indexes and records with chunked
`get_many()` (≤ 500 keys), and reads Redis event **tails** (`LRANGE -1 -1`,
bounded reverse window on corrupt tails) instead of full histories. Pruning a
stale index re-reads Redis and drops only IDs already known expired so a job
enqueued during the read is not overwritten. Sort, queue positions, seven-day
retention, SSE `inbox` events / 1s wait / keep-alive, and “clear failed”
including administrator AI import failures are unchanged. Isolated 100 / 500
p95 is in Stage 7; `claimed_gains` stays false.

## Stage 2 (polling dedupe, cancel, visibility)

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
| 7e | `5db5e8df44db0889f75a72af08fd3a5fead2e746` | Same-protocol 3-round before; 60-minute soak; `soak` CLI |

Before SHA is harness-only `e07dbd094786c4d2a0f4cef39c89bc4a69c5889e` (pre-inbox
batch). After SHA for inbox/overview/locale/prune behavior is through
`686f863ddf6e89f6f97408ec0d6a4216aac08035`; later 7b–7e commits are
measurement and harness only. Same seed manifest, isolated DB/Redis,
loopback ASGI. `claimed_gains` stays **false**.

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

Round `21197fe` application commits revert newest-first (`17c0706`,
`ebdfcaa`, `f533781`, `1156678`, `e33a602`, `bf44716`, `d92a269`,
`87e3b43`, `bac25c5`, `3fe3878`, `24c478c`, `84cd598`, `216dcb7`).
Realtime harness-only commits revert newest-first (`d4bb00a`,
`13391d3`, `8d10320`, `480572a`, `ec2cfe0`, then `025bd71` if desired). Earlier Stage 1–5 commits revert
newest-first (`686f863`, `5077734`, `0796ee6`, `1b9c47f`, `7dc61fa`, then
the original harness if desired). This round added **no** Alembic revision.
If a later index revision exists, keep it and add a forward migration to
drop the index; do not run an old image that does not recognize that
revision.

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
- `REDIS_POOL_SIZE` stays **10** (production default). Thirty and ten
  concurrent inbox SSE snapshots exhaust that pool; even one session
  delivered no events. Do not raise the pool to make the realtime
  report look populated.

## Gates (not yet claimed)

`claimed_gains` stays **false**. Historical Stage 7 numbers (including
inbox p95 ≈ 6427 ms and fleet-100 −81.2%) stay in the archive above.
They are **not** this round's starting measurement. Machine-readable
copy: `reports/perf/round-21197fe-comparison.json`.

Fleet-500 same-protocol 60 s / 300 s × 3 on isolated `55442` / `56389`
(`cs2_perf` / `perf:`), 0 HTTP errors:

| Route | starting median p95 | HEAD median p95 | Δ | 20% | envelope |
| --- | ---: | ---: | ---: | --- | --- |
| inbox | 2156 ms | 2210 ms | −2.5% | fail | inside |
| market | 7.54 ms | 7.10 ms | +5.8% | fail | inside |
| overview | 3.55 ms | 3.72 ms | −4.6% | fail | inside |
| servers | 5.81 ms | 5.79 ms | +0.2% | fail | inside |
| mixed inbox | 2603 ms | 2750 ms | −5.7% | fail | outside |
| mixed overview | 20.8 ms | 25.5 ms | −22.3% | fail | inside 20 ms floor |
| mixed market | 86.2 ms | 153 ms | −77.4% | fail | outside |
| mixed servers | 107 ms | 154 ms | −44.2% | fail | outside |

Fleet-100 same-protocol 60 s / 300 s × 3 (`market-1000` / `history max`),
0 HTTP errors:

| Route | starting median p95 | HEAD median p95 | Δ | 20% | envelope |
| --- | ---: | ---: | ---: | --- | --- |
| inbox | 406 ms | 413 ms | −1.8% | fail | inside |
| overview | 3.35 ms | 3.37 ms | −0.6% | fail | inside |
| market | 4.78 ms | 4.81 ms | −0.6% | fail | inside |
| servers | 5.46 ms | 5.52 ms | −1.1% | fail | inside |
| mixed inbox | 506 ms | 515 ms | −1.9% | fail | inside |
| mixed overview | 141 ms | 141 ms | +0.2% | fail | inside |
| mixed market | 145 ms | 145 ms | −0.3% | fail | inside |
| mixed servers | 144 ms | 143 ms | +0.2% | fail | inside |

Fleet-10 same-protocol 60 s / 300 s × 3 (`market-100` / `history max`),
0 HTTP errors:

| Route | starting median p95 | HEAD median p95 | Δ | 20% | envelope |
| --- | ---: | ---: | ---: | --- | --- |
| inbox | 36.7 ms | 36.9 ms | −0.5% | fail | inside |
| overview | 3.30 ms | 3.34 ms | −1.1% | fail | inside |
| market | 4.59 ms | 4.61 ms | −0.5% | fail | inside |
| servers | 3.81 ms | 3.82 ms | −0.2% | fail | inside |
| mixed inbox | 54.8 ms | 55.8 ms | −1.8% | fail | inside |
| mixed overview | 22.1 ms | 21.8 ms | +1.4% | fail | inside |
| mixed market | 25.3 ms | 24.6 ms | +2.7% | fail | inside |
| mixed servers | 23.0 ms | 22.4 ms | +2.4% | fail | inside |

- Marketplace indexes: HTTP +15.4% (gate 20%), **no Alembic revision**
- Realtime 30 / 10 / 1 sessions (fleet-500 / 100 / 10): both trees
  open the sockets and deliver **0** events. `REDIS_POOL_SIZE` stays
  10.
- Visible-poll request identity and shared cookie helpers: unit tests
  on this tree
- Catalog compact JSON 142,964 / 139,750 bytes (en-US / zh-CN); login
  subset 1,653 / 1,582; overview chrome 18,521 / 18,291
- Bundle budgets and `cacheComponents` / `partialPrefetching` unchanged
- 60-minute fleet-500 soak on HEAD: **RSS fail** (934 MiB after first
  load window → 1731 MiB at 60 min, +85%, gate +5%). HTTP 0 errors.
  Fleet-10 / 100 / 500 API series are recorded (all missed 20%).
  Realtime 0 events at 1 / 10 / 30 sessions. Production browser
  5 / 30 × 3 passed. Isolated 1000 / 1001 seed is recorded. Next:
  `check_baseline`.

Application changes revert by commit. This round added **no** Alembic
revision. Push, deploy, and live restart are out of scope.
