import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";
// Keep the extension in the config import: Next compiles this file to ESM
// before running it in the production Docker builder, where Node's resolver
// does not add ``.ts`` automatically.
import { lanDevOrigins } from "./dev-origins.ts";
import { internalApiUrl } from "./src/shared/config/internal-api.ts";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

/**
 * Internal FastAPI origin. In the same-origin three-service topology (Caddy →
 * Next → FastAPI) this points at the private API listener; in local dev it is
 * the developer's FastAPI instance. The browser never talks to it directly:
 * all API traffic is proxied through Next `rewrites` so cookies stay first
 * party and there is no CORS surface.
 *
 * `next dev` reads this from `frontend/.env` at process start. Production
 * standalone also honors runtime `INTERNAL_API_URL` via
 * `scripts/with-internal-api-url.mjs` (rewrites are otherwise baked at build).
 */
const INTERNAL_API_URL = internalApiUrl();

// A deployment id lets Next detect a stale browser bundle during rolling
// deployments and perform a hard refresh before it keeps submitting old
// Server Action references. Docker/release builds set this to the immutable
// commit SHA; local development may override it explicitly.
const DEPLOYMENT_ID = [process.env.NEXT_DEPLOYMENT_ID, process.env.GIT_SHA]
  .find((value) => value && value !== "unknown");
const isolatedCandidate = process.env.CC_TEST_BUILD === "1" && process.env.CC_CANDIDATE_BUILD === "1";

const nextConfig: NextConfig = {
  output: "standalone",
  deploymentId: DEPLOYMENT_ID,
  reactStrictMode: true,
  poweredByHeader: false,
  typedRoutes: true,
  // Handwritten frontend/AGENTS.md instructions use no managed markers.
  // Next 16.4 removes managed blocks when agentRules is disabled.
  agentRules: false,
  // Preserve the actual internal origin: NextURL normalizes loopback IPs to
  // localhost, which turns a locale rewrite into an external self-proxy.
  skipProxyUrlNormalize: true,
  // `localhost` and `127.0.0.1` are different Origins. Without this, opening
  // http://127.0.0.1:31800 or a LAN IP blocks `/_next/static` and the login
  // CAPTCHA never hydrates. Also allows the INTERNAL_API_URL / PUBLIC_APP_URL
  // host so a LAN or Docker access name does not need ALLOWED_DEV_ORIGINS.
  // Extra hosts: ALLOWED_DEV_ORIGINS=host1,host2
  allowedDevOrigins: lanDevOrigins(),
  // Formal defaults stay off until functional, instant-navigation and RSS gates pass.
  cacheComponents: isolatedCandidate,
  partialPrefetching: isolatedCandidate,
  experimental: {
    exposeTestingApiInProductionBuild: isolatedCandidate && process.env.CC_EXPOSE_TEST_API === "1",
  },
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
        // Plugin archives and host setup libraries on FastAPI `/static`.
        // Tutorial screenshots ship from `frontend/public/tutorial/`.
        { source: "/static/:path*", destination: `${INTERNAL_API_URL}/static/:path*` },
      ],
      afterFiles: [],
      fallback: [],
    };
  },
};

export default withNextIntl(nextConfig);
