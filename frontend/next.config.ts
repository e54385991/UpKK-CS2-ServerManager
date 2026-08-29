import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";
import { lanDevOrigins } from "./dev-origins";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

/**
 * Internal FastAPI origin. In the same-origin three-service topology (Caddy →
 * Next → FastAPI) this points at the private API listener; in local dev it is
 * the developer's FastAPI instance. The browser never talks to it directly:
 * all API traffic is proxied through Next `rewrites` so cookies stay first
 * party and there is no CORS surface.
 */
const INTERNAL_API_URL =
  process.env.INTERNAL_API_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  typedRoutes: true,
  // Keep the authored frontend/AGENTS.md stable; next dev would otherwise
  // rewrite the header block on every start.
  agentRules: false,
  // `localhost` and `127.0.0.1` are different Origins. Without this, opening
  // http://127.0.0.1:3000 or a LAN IP blocks `/_next/static` and the login
  // CAPTCHA never hydrates. Extra hosts: ALLOWED_DEV_ORIGINS=host1,host2
  allowedDevOrigins: lanDevOrigins(),
  // cacheComponents / partialPrefetching are intentionally OFF. See
  // frontend/AGENTS.md ("Caching & navigation"): every route is authenticated
  // and locale-cookie driven, so the shell is inherently dynamic and gains
  // little from a prerendered App Shell; enabling the flags fails prerender for
  // the cookie reads, and the upstream cacheComponents memory-growth gate from
  // the plan is unmet. Non-blocking navigation is delivered via the shared App
  // Shell + per-route loading.tsx + Suspense + <Link> prefetch.
  // This app lives in a monorepo alongside the FastAPI backend; pin the
  // Turbopack root to this package so Next does not infer the repository root.
  turbopack: {
    root: import.meta.dirname,
  },
  logging: {
    fetches: { fullUrl: false },
  },
  async rewrites() {
    return {
      beforeFiles: [
        // Proxy the versioned API and realtime channels to FastAPI so the
        // browser only ever sees the Next origin (first-party cookies, no CORS).
        { source: "/api/:path*", destination: `${INTERNAL_API_URL}/api/:path*` },
        { source: "/health", destination: `${INTERNAL_API_URL}/health` },
        // Tutorial / help screenshots still live on FastAPI `/static`.
        { source: "/static/:path*", destination: `${INTERNAL_API_URL}/static/:path*` },
      ],
      afterFiles: [],
      fallback: [],
    };
  },
};

export default withNextIntl(nextConfig);
