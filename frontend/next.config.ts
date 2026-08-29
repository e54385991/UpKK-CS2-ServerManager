import type { NextConfig } from "next";

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
      ],
      afterFiles: [],
      fallback: [],
    };
  },
};

export default nextConfig;
