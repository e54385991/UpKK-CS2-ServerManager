<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This project uses **Next.js 16.3.3**, which has breaking changes versus older
Next.js — APIs, conventions, and file structure may differ from your training
data. **Before writing any Next.js code, read the version-matched guide under
`node_modules/next/dist/docs/`** (e.g. `dist/docs/01-app/...`). Heed deprecation
notices (for example, the request-interception file is `proxy.ts`, not
`middleware.ts`).

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

## Backend contract & auth

- **All** browser→backend traffic is proxied by Next `rewrites` in
  `next.config.ts` (`/api/*`, `/health` → `INTERNAL_API_URL`). This keeps
  cookies first-party and removes CORS. The browser never calls FastAPI
  directly.
- Session: the backend sets an HttpOnly cookie `upkk_access_token` whose value is
  the JWT. `src/modules/auth/session.ts` resolves the user via
  `GET /api/auth/me` with a bearer header; `src/proxy.ts` cheaply guards console
  routes on cookie presence; layouts validate for real.
- Server-side calls use `src/shared/api/server-fetch.ts` (`apiFetch`), which
  attaches the bearer from the cookie and returns structured `ApiResult`
  (never throws for auth/transport failures — pages render a degraded state).
- HTTP contract source of truth is FastAPI's OpenAPI. Regenerate types with
  `npm run gen:api` (expects `../openapi.json`) into
  `src/shared/api/schema.d.ts`. As `/api/v1` lands, adapt the per-domain
  `api.ts` mappers rather than leaking raw backend shapes into the UI.

## Styling

- Design tokens (tactical-ops dark theme: graphite surfaces, cyan primary,
  restrained status colors) are defined once in `src/app/globals.css` under
  `@theme`. Use token utilities (`bg-surface`, `text-fg-muted`, `border-line`,
  `text-primary`, …) instead of hard-coded colors.
- Compose class names with `cn()` from `src/shared/lib/cn.ts`.

## Environment

Copy `.env.example` → `.env`. Key variables:

- `INTERNAL_API_URL` — FastAPI origin Next proxies to (server-side only).
- `PUBLIC_APP_URL` — public origin for absolute URLs / OAuth redirects.

## Commands

```bash
npm run dev        # Turbopack dev server on :3000
npm run build      # production build (fails on type errors)
npm run start      # serve the production build on :3000
npm run lint       # ESLint (flat config)
npm run typecheck  # tsc --noEmit
npm run gen:api    # regenerate OpenAPI types from ../openapi.json
```

Before marking frontend work done, `npm run lint`, `npm run typecheck`, and
`npm run build` must all pass.

## Roadmap (phased parity)

This app is being built to full parity with the legacy UI in phases: server
lifecycle & monitoring, plugins/maps/files/console, AI/Discord, settings/audit,
full zh-CN/en-US i18n, then visual/perf acceptance and the root-path cutover.
Placeholder routes render an explicit "建设中" state; replace them as each
domain's `/api/v1` contract lands.
