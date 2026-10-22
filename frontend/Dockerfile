# Compile Next on the build host. QEMU amd64 `next build` hangs on arm64
# builders; the runner stage still uses the target-arch Node binary. Use the
# stable default Turbopack bundler in Docker, development and CI alike.
# Node 26.12.1 is the current stable release (Alpine 3.24, multi-arch index).
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ARG DEPLOYMENT_ID=unknown
FROM --platform=$BUILDPLATFORM node:26.12.1-alpine3.24@sha256:9bd229b644e391df00fb989efa732335aefc6170a05d8c163c5c8ca9870da401 AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm install --global "$(node -p 'require("./package.json").packageManager')" \
    && npm ci

FROM --platform=$BUILDPLATFORM node:26.12.1-alpine3.24@sha256:9bd229b644e391df00fb989efa732335aefc6170a05d8c163c5c8ca9870da401 AS builder
WORKDIR /app
ARG GIT_SHA
ARG DEPLOYMENT_ID
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
COPY . .
RUN --mount=type=secret,id=next_server_actions_key \
    mkdir -p public && \
    next_build_deployment_id="${DEPLOYMENT_ID}" && \
    if [ -z "${next_build_deployment_id}" ] || [ "${next_build_deployment_id}" = "unknown" ]; then \
        next_build_deployment_id="${GIT_SHA}"; \
    fi && \
    if [ "${next_build_deployment_id}" = "unknown" ]; then next_build_deployment_id=""; fi && \
    NEXT_DEPLOYMENT_ID="${next_build_deployment_id}" \
    NEXT_SERVER_ACTIONS_ENCRYPTION_KEY="$(cat /run/secrets/next_server_actions_key 2>/dev/null || true)" \
    npx next build

FROM node:26.12.1-alpine3.24@sha256:9bd229b644e391df00fb989efa732335aefc6170a05d8c163c5c8ca9870da401 AS runner
WORKDIR /app
ARG GIT_SHA
ARG BUILD_TIME
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
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
