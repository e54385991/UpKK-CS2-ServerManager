<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This project uses **Next.js 16.3.3**, which has breaking changes versus older
Next.js — APIs, conventions, and file structure may differ from your training
data. **Before writing any Next.js code, read the version-matched guide under
`node_modules/next/dist/docs/`** (e.g. `dist/docs/01-app/...`). Heed deprecation
notices (for example, the request-interception file is `proxy.ts`, not
`middleware.ts`). `next.config.ts` sets `agentRules: false` so `next dev`
does not rewrite this file.

<!-- END:nextjs-agent-rules -->

# CS2 Server Manager — Frontend (Next.js console)

A dedicated, modern console for the CS2 Server Manager. It replaces the legacy
Jinja/Bootstrap UI and talks to the FastAPI backend through a same-origin proxy.

## Tech stack (pinned)

| Area | Choice | Notes |
| --- | --- | --- |
| Framework | `next@16.3.3` (App Router, Turbopack) | Read `node_modules/next/dist/docs/` first. |
| UI runtime | `react`/`react-dom@19.2.8` | React Compiler-era; the `react-hooks` lint rules are strict. |
| Language | `typescript@5.9.3` | **Deliberate pin.** TS 7 is installed as latest on the registry, and `tsc`/Next build support it, but `typescript-eslint` does not yet support TS ≥ 7.1 (tracking: typescript-eslint#10940). Pinning 5.9.3 keeps build + typecheck + **lint** all green. Move to 7.x once typescript-eslint supports it. |
| Styling | `tailwindcss@4.3.3` + `@tailwindcss/postcss` | v4 engine; design tokens live in `src/app/globals.css` under `@theme`. |
| Lint | `eslint@9.39.5` + `eslint-config-next@16.3.3` | Flat config. ESLint 10 is incompatible with the TS-ESLint parser's scope manager here — stay on ESLint 9. |
| Icons | `lucide-react` | Import per-icon; never pass an icon **component** across the server→client boundary (see RSC rules). |
| Docker runtime | `node:26.8.1-alpine` | Production image + CI use Node 26 Current. |

Dependencies are pinned to exact versions and committed with `package-lock.json`.
Do not broadly upgrade or rewrite the lockfile without a reason.

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

## RSC / Client boundaries (read this before adding components)

- Server Components are the default. Add `"use client"` only when a component
  needs state, effects, browser APIs, or event handlers.
- **Never pass a function or component as a prop from a Server Component to a
  Client Component.** This includes `lucide-react` icons. If a client component
  needs an icon that varies by data (e.g. the sidebar), make that component a
  Client Component and import the icon there, or pass a pre-rendered
  `ReactNode`. (The sidebar is a Client Component for exactly this reason.)
- Anything reading the request (cookies, headers) is dynamic. Server data access
  lives in `*/api.ts` module files marked `import "server-only"`.

## Non-blocking navigation

Instant navigation is achieved with plain App Router primitives — no
experimental flags required:

- The console **App Shell** (`src/app/(console)/layout.tsx`: `Sidebar` +
  `Topbar`) is rendered by the shared layout and stays mounted across route
  changes; only the page region re-renders.
- Every data route provides a `loading.tsx` skeleton and wraps server data in
  `<Suspense>`, so the shell paints immediately while data streams in.
- Navigation uses `<Link>` (default prefetch) and typed routes
  (`typedRoutes: true`). Runtime path strings must be asserted `as Route`.

Do not reintroduce blocking, all-or-nothing server waits at the layout level.

### Caching & navigation: why `cacheComponents` is off

`cacheComponents` and `partialPrefetching` are intentionally disabled in
`next.config.ts`. Evidence-based decision:

- Every console route is authenticated and locale is read from the `locale`
  cookie in the root layout, so the shell is inherently per-session dynamic.
  Enabling `cacheComponents` fails the production build during prerender
  ("uncached or runtime data during prerendering" for the layout's `cookies()`
  reads), and would require wrapping the session/i18n reads in Suspense or
  `use cache` app-wide for little benefit on a fully-dynamic console.
- The agreed plan gates `cacheComponents` behind an upstream memory-growth
  report (a hard memory stress-test gate) that is unmet.

Non-blocking, instant navigation is already delivered without these flags via
the shared App Shell (persistent sidebar/topbar), per-route `loading.tsx`
skeletons, `<Suspense>`-streamed server data, and `<Link>` prefetch. Verified
with Lighthouse: Performance 100 and CLS 0 on `/login` and `/overview`. Revisit
`cacheComponents` once it is stable for cookie-driven apps and the memory gate
is cleared; adopt it per `node_modules/next/dist/docs/01-app/02-guides/adopting-partial-prefetching.md`.

## Backend contract & auth

- **All** browser→backend traffic is proxied by Next `rewrites` in
  `next.config.ts` (`/api/*`, `/health`, `/static/*` → `INTERNAL_API_URL`).
  This keeps cookies first-party and removes CORS. The browser never calls
  FastAPI directly. Set `INTERNAL_API_URL` in `frontend/.env` for `next
  dev`. Production standalone applies the same variable at process start
  (`scripts/with-internal-api-url.mjs`) so Docker/compose can point at
  `http://app:8000` without rebuilding the image.
- Session: the backend sets an HttpOnly cookie `upkk_access_token` whose value is
  the JWT. `src/modules/auth/session.ts` resolves the user via
  `GET /api/auth/me` with a bearer header; `src/proxy.ts` cheaply guards console
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
  cookie. Long-running server actions and market plugin installs are a
  **delivery queue**: POST returns **202** with an `operation_id` and the
  page must not block. Jobs run **one at a time per server** so SSH locks
  and plugin extracts do not overlap. The live log is the existing
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

Bilingual **zh-CN (default) + en-US** via `next-intl`, without URL routing:

- Active locale comes from the `locale` cookie (SSR-authoritative), falling back
  to `Accept-Language`, then the default — see `src/i18n/request.ts` and
  `src/i18n/config.ts`. The Next plugin is wired in `next.config.ts`.
- Message catalogs live in `src/i18n/messages/{zh-CN,en-US}.json` and MUST keep
  identical key sets. Namespaces: `site`, `nav`, `shell`, `login`, `overview`,
  `servers`, `serverDetail`, `serverNew`, `plugins`, `assistant`, `settings`,
  `audit`, `profile`. The `settings` namespace covers the admin system-settings
  form (proxy, GitHub token, SMTP/Gmail). The `plugins` namespace covers the
  marketplace catalog, install preflight, and the per-server installed list.
- Server Components: `const t = await getTranslations("ns")`. Client Components:
  `const t = useTranslations("ns")`. The root layout provides messages via
  `NextIntlClientProvider` and sets `<html lang>`.
- The topbar `LanguageSwitcher` writes the `locale` cookie and calls
  `router.refresh()`. Do not hardcode user-facing strings — add a key to both
  catalogs instead. Because the layout reads the cookie, routes render
  dynamically (expected for this authenticated console).

## Styling

- Design tokens (tactical-ops dark theme: graphite surfaces, cyan primary,
  restrained status colors) are defined once in `src/app/globals.css` under
  `@theme`. Use token utilities (`bg-surface`, `text-fg-muted`, `border-line`,
  `text-primary`, …) instead of hard-coded colors.
- Compose class names with `cn()` from `src/shared/lib/cn.ts`.

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

## Commands

```bash
npm run dev          # Turbopack dev server on :3000
npm run build        # production build (fails on type errors)
npm run start        # serve the production build on :3000
npm run build:start  # production build, then start
# Repo-root ./start.sh also accepts: api | dev | build | start | build+start
npm run lint       # ESLint (flat config)
npm run typecheck  # tsc --noEmit
npm run gen:api    # regenerate OpenAPI types from ../openapi.json
```

Before marking frontend work done, `npm run lint`, `npm run typecheck`, and
`npm run build` must all pass.

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
