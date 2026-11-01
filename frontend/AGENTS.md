# This is NOT the Next.js you know

This project uses **Next.js 16.4.3**, which has breaking changes versus older
Next.js — APIs, conventions, and file structure may differ from your training
data. **When changing Next.js behavior** (for example routing, rendering,
caching, or build/runtime configuration), read the relevant version-matched
sections under `node_modules/next/dist/docs/` (e.g. `dist/docs/01-app/...`).
Reuse sections already read in this task unless the version or topic changes;
text, styling, and instruction-only edits do not require a full manual read.
If bundled docs are missing, check the installed/locked version and its official
documentation; do not upgrade dependencies just to obtain documentation. Heed deprecation
notices (for example, the request-interception file is `proxy.ts`, not
`middleware.ts`). `next.config.ts` sets `agentRules: false`. Keep these authored
instructions outside Next.js managed markers: since 16.4, `next dev` removes
existing managed blocks when that option is disabled.

# CS2 Server Manager — Frontend (Next.js console)

A dedicated, modern console for the CS2 Server Manager. It talks to the FastAPI
backend through a same-origin proxy.

## Tech stack (pinned)

| Area | Choice | Notes |
| --- | --- | --- |
| Framework | `next@16.4.3` (App Router, Turbopack) | Consult the matching bundled docs when changing Next.js behavior. |
| UI runtime | `react`/`react-dom@19.3.1` | React Compiler-era; the `react-hooks` lint rules are strict. |
| Language | `typescript@6.0.3` | Deliberate compatibility pin: the current `typescript-eslint` toolchain supports TypeScript `<6.1`, so TypeScript 7 is not yet usable. |
| Styling | `tailwindcss@4.3.3` + `@tailwindcss/postcss` | v4 engine; design tokens live in `src/app/globals.css` under `@theme`. |
| Lint | `eslint@9.39.5` + `eslint-config-next@16.4.3` | React, import, and JSX accessibility plugins currently declare peer support through ESLint 9. |
| Icons | `lucide-react` | Import per-icon; never pass an icon **component** across the server→client boundary (see RSC rules). |
| Package manager | `npm@11.21.1` | `package.json#packageManager` is shared by CI and Docker builds. |
| Docker runtime | `node:26.13.0-alpine3.24` | Production image + CI use Node 26 LTS. |

Dependencies are pinned to exact versions and committed with `package-lock.json`.
Use the npm version declared in `packageManager` (`npm install --global npm@11.21.1`)
for local installs; CI and Docker read that declaration before running `npm ci`.
For an authorized dependency upgrade, update all packages to the latest stable
versions compatible with upstream and peer constraints, then regenerate the
lockfile normally. Do not force peer dependencies or add overrides to bypass
those constraints; record any version retained for compatibility.
Recheck these constraints during each upgrade, including transitive dependencies.
Dependabot monitors this Dockerfile alongside npm; keep the newest compatible
stable Node release pinned by version and the verified multi-architecture digest.
The backend uses the latest stable Python 3.15 patch release; the former Docker
RC exception is historical and no longer applies to production images.

## Architecture: `app → modules/<domain> → shared`

Strict one-way dependency direction, enforced by ESLint `no-restricted-imports`:

- `src/app/**` — routing and composition only (layouts, pages, `loading.tsx`).
  Pages orchestrate; they hold no business logic.
- `src/modules/<domain>/**` — feature domains (`auth`, `servers`, `overview`,
  `shell`, …). Each owns its data access, types, and components. Modules may use
  `shared` but must **not** import from `app`.
- `src/shared/**` — cross-cutting primitives: `ui/` (design-system components),
  `lib/` (utilities like `cn`), `config/` (navigation, site), `api/`
  (server-side fetch + generated OpenAPI types). Shared must not import from
  `modules` or `app`.

When adding a feature, create/extend a module under `src/modules/`; keep route
files thin.

## RSC / Client boundaries (consult when adding or changing component boundaries)

- Server Components are the default. Add `"use client"` only when a component
  needs state, effects, browser APIs, or event handlers.
- **Never pass a function or component as a prop from a Server Component to a
  Client Component.** This includes `lucide-react` icons. If a client component
  needs an icon that varies by data (e.g. the sidebar), make that component a
  Client Component and import the icon there, or pass a pre-rendered
  `ReactNode`. (The sidebar is a Client Component for exactly this reason.)
- Anything reading the request (cookies, headers) is dynamic. Server data access
  lives in `*/api.ts` module files marked `import "server-only"`.

## Non-blocking navigation and Cache Components

Console chrome lives in `src/app/[locale]/(console)/layout.tsx`. It renders the
sidebar, topbar and content container before request-owned data. Authentication
only redirects in `SessionGate`; the backend still authorizes each data request
and mutation. Every console page checks the session on its own RSC request,
even when a parent layout is reused. React `cache()` deduplicates only within
that render; never cache users, permissions or JWTs across requests or add
`use cache: private` for them.

Move `params`, `searchParams`, cookies, headers and uncached API reads into
small data components behind meaningful localized Suspense fallbacks. Keep
common headings and structural elements outside those boundaries. Do not use
an empty shell, whole-page fallback or `instant = false` to pass validation.
Use existing skeletons and preserve the layout at 390 and 1440 pixel widths.
Business calls remain `no-store`, with their existing timeout, bearer forwarding
and degraded results. Shared content is local, non-user content keyed by locale.

Cookie authentication is compatible with Cache Components: Proxy selects the
locale, `next/root-params` makes it available without reading cookies in the
root layout, and authentication/data reads stream in their own boundaries.
The formal defaults remain off until the current functional, production
navigation and independent Next RSS acceptance gates pass. Isolated candidate
builds use `CC_TEST_BUILD=1` plus `CC_CANDIDATE_BUILD=1`; only dedicated navigation
builds may set `CC_EXPOSE_TEST_API=1`. Production and RSS builds never expose
the testing API. See `instant-nav.rig.md` and
`../docs/NEXT_CACHE_COMPONENTS_ACCEPTANCE.md`; short tests cannot clear the
three-round 5% RSS gate.

Use the centralized `@/i18n/navigation` Link/router/pathname adapters and
`PublicRoute` types derived from `next typegen`. Preserve default shared-shell
prefetch, `prefetch={false}` and existing hover/focus intent. Do not add full
business-data prefetch. Server Actions call `revalidatePublicPath` so both
internal languages are invalidated. Typecheck always runs `next typegen` first.

## Backend contract & auth

- **All** browser→backend traffic is proxied by Next `rewrites` in
  `next.config.ts` (`/api/*`, `/health`, `/static/*` → `INTERNAL_API_URL`).
  This keeps cookies first-party and removes CORS. The browser never calls
  FastAPI directly. Set `INTERNAL_API_URL` in `frontend/.env` for `next
  dev`. Production standalone applies the same variable at process start
  (`scripts/with-internal-api-url.mjs`) so Docker/compose can point at
  `http://app:8000` without rebuilding the image. 1Panel multi-instance
  compose keeps Next and Caddy on a private network so `app:8000` cannot
  resolve to another install on `1panel-network`.
- Session: the backend sets an HttpOnly cookie `upkk_access_token` (or
  `upkk_access_token_<SESSION_COOKIE_SUFFIX>` when two consoles share a
  host). Its value is
  the JWT. `src/modules/auth/session.ts` resolves the user via
  `GET /api/v1/auth/me` with a bearer header; `src/proxy.ts` cheaply guards console
  routes on cookie presence; layouts validate for real.
- Server-side calls use `src/shared/api/server-fetch.ts` (`apiFetch`), which
  attaches the bearer from the cookie and returns structured `ApiResult`
  (never throws for auth/transport failures — pages render a degraded state).
- HTTP contract source of truth is FastAPI's OpenAPI, snapshotted at
  `tests/baselines/openapi.json` (regenerate that with the backend's
  `scripts/update_contract_baselines.py` whenever the API changes). Regenerate
  the TS client with `npm run gen:api` into `src/shared/api/schema.d.ts`; import
  DTO aliases from `src/shared/api/types.ts`.
- The browser-facing contract is the versioned **`/api/v1`** surface
  (`/api/v1/auth/me`, `/api/v1/servers` (GET list + POST create),
  `/api/v1/servers/{id}`,
  `/api/v1/overview/summary`, `/api/v1/audit`, `/api/v1/settings`,
  `/api/v1/settings/test-email`, `/api/v1/settings/gmail/*`,
  `/api/v1/servers/{id}/operations` and its current/logs/lock/SSE children,
  `/api/v1/plugins/market` (+ categories/detail),
  `/api/v1/servers/{id}/plugins`,
  `/api/v1/servers/{id}/plugins/market/{pluginId}/preflight`,
  `/api/v1/servers/{id}/plugins/market/{pluginId}/install`),
  which returns non-secret projections. Secret mutation (GitHub token, SMTP
  password, Gmail credentials) is write-only: the GET view exposes presence
  flags and a token prefix, never the secret itself. Creating a server also
  treats SSH/RCON/GSLT as write-only. Mutations from the
  console go through Next.js Server Actions in `src/modules/<domain>/actions.ts`,
  which attach the session JWT as Bearer — the browser never reads the HttpOnly
  cookie. Long-running server actions (market/GitHub plugin installs, archive
  extract, URL download to the host, cleanup delete / system apply, plugin
  auto-update run/test, plugin diagnostics execute/restore/resume) are a
  **delivery queue**: POST returns **202** with an `operation_id` and the
  page must not block. Jobs run **one at a time per server** so SSH locks
  and plugin extracts do not overlap. Scheduled lifecycle / `backup_plugins`
  and fleet batch restart/stop/update/framework install enqueue onto the same
  per-server FIFO and wait there, instead of taking `maintenance_lock`.
  Short file mutations (list, edit, mkdir, rename, copy, single-file delete,
  upload) and cleanup **scan** stay on their existing HTTP / SSE paths. The live log is the existing
  replayable SSE stream — do not add a second WebSocket for panel jobs, and
  do not attach the task tray to tmux (tmux is only the game/SteamCMD pane
  via `/live-console/{id}`). `EventSource` cannot set `Authorization`, so
  the console connects to
  `/ops-stream/servers/{id}/operations/{operationId}` (a Next Route Handler
  that upgrades the session cookie to Bearer). The FastAPI SSE GET also
  accepts the session cookie as a same-origin fallback.
  `GET /api/v1/operations/inbox` feeds the **top-right activity tray** in
  the App Shell (`ActivityTray` in `Topbar`). While any job is queued or
  running the tray must show a **clear pulse animation** and the
  **remaining task count**. Opening the tray shows the submitted command,
  status, current step, and console output (SSE, with journal fallback).
  Failed jobs are summarized on a separate **Failed** tab, retained for
  **7 days**, and can be dismissed one-by-one or cleared in bulk.
  After a 202, call `trackQueuedOperation` so the tray opens immediately.
  Each domain `api.ts` maps the snake_case DTO to a camelCase domain type so
  the UI stays decoupled from wire casing; extend those mappers as more
  `/api/v1` lands.

## Internationalization (i18n)

Bilingual **zh-CN + en-US (default)** via `next-intl`, with public URLs preserved:

- Pages and the root layout live below `[locale]`. Proxy internally rewrites
  public paths using a valid `locale` cookie, otherwise the existing highest
  quality Accept-Language rule, otherwise `en-US`. Only those two root values
  are generated. Explicit internal prefixes are normalized before the auth
  presence check; API, static files and JSON/stream/upload handlers stay outside
  locale rewriting. Never trust incoming `x-upkk-public-path`; Proxy sets it.
- Server configuration reads `next/root-params`, not cookies or headers, for
  locale. `generateMetadata` gets language from that configuration; request Host
  remains request-owned and is never cached across requests.
- Catalogs in `src/i18n/messages/{zh-CN,en-US}.json` keep identical keys. Preserve
  the monitoring catalog merge before namespace trimming. Root, console, auth
  and domain `ClientMessages` providers select explicit namespaces; nested
  providers replace dictionaries instead of merging them. Browser code does
  not download another locale JSON.
- Language switching uses the locale-aware navigation API while preserving the
  public pathname, query and hash. Keep local 404/error recovery and public login
  return addresses. No user-facing strings are hardcoded.

## Styling

- Design tokens (tactical-ops dark theme: graphite surfaces, cyan primary,
  restrained status colors) are defined once in `src/app/globals.css` under
  `@theme`. Use token utilities (`bg-surface`, `text-fg-muted`, `border-line`,
  `text-primary`, …) instead of hard-coded colors.
- Compose class names with `cn()` from `src/shared/lib/cn.ts`.
- 助手页面消费可回放 SSE 的 `token_usage` 事件，实时显示输入、输出和合计 token；运行中必须有
  可访问的动画状态和当前阶段。只展示安全阶段/进度，不渲染内部 chain-of-thought 或原始秘密。
- 系统 AI 上下文窗口默认 256K，管理端可按模型能力选择 8K、16K、32K、64K、128K、256K、384K 或 1M，并与 OpenAPI 生成类型保持同步。

## Environment

Copy `.env.example` → `.env` in this directory (not the repo root). Restart
Next after changing values. Key variables:

- `INTERNAL_API_URL` — FastAPI origin Next proxies to (server-side only).
  Local default `http://127.0.0.1:8000`; Compose / official 1Panel app uses
  `http://app:8000`. Two separate 1Panel runtimes must not use the host LAN
  IP (Docker hairpin hangs `/api/captcha`); use `host.docker.internal`, the
  backend container name on `1panel-network`, or host-network `127.0.0.1`.
- `PUBLIC_APP_URL` — optional public origin for absolute URLs. When unset
  (or set to a bind address like `0.0.0.0`), Next uses the request Host and
  port. OAuth already uses `window.location.origin`.
- `NEXT_DEPLOYMENT_ID` — optional immutable release identifier. Docker builds
  default it to `GIT_SHA`; Next uses it to detect stale browser bundles during
  rolling deployments and trigger a hard refresh.
- `NEXT_SERVER_ACTIONS_ENCRYPTION_KEY` — optional build-time Base64 AES key
  (decoded length 16, 24, or 32 bytes). Set one stable value for independently
  built or multi-instance deployments so every instance can decrypt the same
  Server Action references. Pass it as a CI/build secret; never commit it or
  expose it as runtime/browser configuration. A single image shared by all
  replicas uses the key embedded in that build. The Docker publish script
  generates one key shared by all target platforms for a publish when this
  value is omitted. The Dockerfile consumes it via a BuildKit secret mount; do
  not convert it back to an `ARG`/`ENV`.

The root footer is a Server Component and displays the frontend package version,
backend API version, a validated seven-character commit prefix, and the UTC
build time. Docker/release builds inject `APP_GIT_SHA` and `APP_BUILD_TIME`; a
missing or malformed value must remain `—` rather than exposing arbitrary
environment text.

## Commands

```bash
npm run dev          # Turbopack dev server on :31800
npm run build        # production build (fails on type errors)
npm run start        # serve the production build on :31800
npm run build:start  # production build, then start
# Repo-root ./start.sh also accepts: api | dev | build | start | build+start
npm run lint       # ESLint (flat config)
npm run typecheck  # next typegen + tsc --noEmit
npm run test:unit  # TypeScript module tests (Node's built-in test runner)
npm run gen:api    # regenerate OpenAPI types from ../tests/baselines/openapi.json
npx playwright test --config=playwright.overview.config.ts # isolated streaming regression
npx playwright test --config=playwright.monitor.config.ts    # isolated settings monitoring charts
# Production measurement (requires npm run build; not part of default CI):
# PERF_WARMUP=5 PERF_MEASURE=30 PERF_ROUNDS=3 \
#   PERF_BROWSER_ROUTES=login,overview,servers,activity-tray,plugins-install,assistant,files \
#   PERF_WIDTHS=390,1440 \
#   npx playwright test --config=playwright.production-baseline.config.ts
```

Completion scope is defined in the root `AGENTS.md` under **Task Completion
Checks**. Its full baseline already runs `npm run test:unit`, `npm run lint`,
`npm run typecheck`, `npm run build`, and `npm run check:bundle`; a successful
run satisfies these frontend gates without a second execution. For an isolated
frontend change, run only the applicable commands and tests. Run
`npm run check:bundle` when the change can affect client bundles or route
loading, rather than after every production build. The bundle check
checks the gzip size of every route's initial client
chunks (253 KiB per route, 150 KiB per chunk). Heavy editors and terminal
libraries must remain in lazy chunks. The CI `frontend-playwright-smoke` job also
runs the isolated overview configuration after the public-page smoke. It starts
its own mock backend and Next dev server on loopback ports 38121/31821 (override
with `OVERVIEW_MOCK_PORT` / `OVERVIEW_TEST_PORT`); never point this fixture at a
live backend or run a production build concurrently against the same `.next`.
The same job also runs `playwright.monitor.config.ts` on 38141/31841
(`MONITOR_MOCK_PORT` / `MONITOR_TEST_PORT`) for the settings monitoring dashboard.
Comparable production timings use `playwright.production-baseline.config.ts` after
`npm run build`; that job is not part of default CI. See `docs/PERFORMANCE.md`.

## DTO and boundary rules

The browser-facing API uses generated OpenAPI types only. Server Components
call `apiFetch` from domain `api.ts`; Client Components receive serializable
DTOs and never receive ORM/database objects, secrets, or server-only functions.
Keep request and response types distinct, map snake_case wire DTOs to camelCase
domain types in the module API layer, and treat `response_model` filtering on
the FastAPI side as a security boundary.

When splitting a large module, keep the public container small and move state
into hooks, pure views, and focused dialogs. Do not bypass the app → modules →
shared direction with path aliases or barrel files that create cycles.

## Roadmap (phased parity)

This app is being built to full parity with the legacy UI in phases: server
lifecycle & monitoring, plugins/maps/files/console, AI/Discord, then
visual/perf acceptance and the root-path cutover. Audit logs, admin system
settings (read/write), the server operations center (202 + `operation_id` +
replayable SSE), the add-server wizard (CAPTCHA + SSH check), the plugin
marketplace (browse + preflight + 202 install), and full zh-CN/en-US i18n
are already implemented.
Placeholder routes render an explicit "in progress" state; replace them as
each domain's `/api/v1` contract lands.
