# Compile Next on the build host. QEMU amd64 `next build` hangs on arm64
# builders; the runner stage still uses the target-arch Node binary.  The
# pinned Next native Turbopack binding is not available on every arm64 build
# host, so release images use the supported Webpack fallback while local
# development/CI keep the faster Turbopack default.
# Node 26.8.1 is the current stable release (alpine, multi-arch index).
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ARG DEPLOYMENT_ID=unknown
FROM --platform=$BUILDPLATFORM node:26.8.1-alpine@sha256:2d984a15c9b54fd0aeb608b8e0d0d83529eb34d2966db27a1fb4f1edc3d298a3 AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM --platform=$BUILDPLATFORM node:26.8.1-alpine@sha256:2d984a15c9b54fd0aeb608b8e0d0d83529eb34d2966db27a1fb4f1edc3d298a3 AS builder
WORKDIR /app
ARG GIT_SHA
ARG DEPLOYMENT_ID
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN --mount=type=secret,id=next_server_actions_key \
    NEXT_DEPLOYMENT_ID="${DEPLOYMENT_ID}" \
    NEXT_SERVER_ACTIONS_ENCRYPTION_KEY="$(cat /run/secrets/next_server_actions_key 2>/dev/null || true)" \
    mkdir -p public && npx next build --webpack

FROM node:26.8.1-alpine@sha256:2d984a15c9b54fd0aeb608b8e0d0d83529eb34d2966db27a1fb4f1edc3d298a3 AS runner
WORKDIR /app
ARG GIT_SHA
ARG BUILD_TIME
ARG DEPLOYMENT_ID
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_DEPLOYMENT_ID=${DEPLOYMENT_ID} \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    APP_GIT_SHA=${GIT_SHA} \
    APP_BUILD_TIME=${BUILD_TIME}
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/scripts/with-internal-api-url.mjs ./with-internal-api-url.mjs
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=10 \
  CMD node -e "fetch('http://127.0.0.1:3000/login').then((r) => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"
CMD ["node", "with-internal-api-url.mjs"]
