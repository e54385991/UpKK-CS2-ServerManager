# Alpine keeps the production image small and avoids shipping Debian's
# perl/apt runtime packages.  The Python 3.14.7 wheels exported by uv include
# musllinux artifacts for every native dependency used by the application.
FROM ghcr.io/astral-sh/uv:0.12.16-alpine@sha256:471f06694d925485af151f8a564d2b0f265bfc5f40a0dd7c295a731fb5c3fb4c AS uv
FROM python:3.14.7-alpine3.24@sha256:016508ba505da24f7139765bc4bb669df4e88eb2f12eeadd571bf2f88d7533df

ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    DEBUG=False \
    RUN_MODE=production \
    APP_GIT_SHA=${GIT_SHA} \
    APP_BUILD_TIME=${BUILD_TIME}

WORKDIR /app

# Pull security fixes that may have landed after the Python base image was
# published without persisting repository indexes or download caches.
RUN apk upgrade --no-cache

# The production export is hash-pinned, so the image build is reproducible
# without installing the development toolchain or frontend dependencies. uv is
# build-time only; pip is removed from the runtime image afterwards.
COPY --from=uv /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/
COPY requirements.txt /tmp/requirements.txt
# Removing pip also removes its vendored msgpack copy from the runtime
# vulnerability inventory.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && rm -rf /usr/local/lib/python3.14/site-packages/pip \
        /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
        /usr/local/lib/python3.14/ensurepip \
        /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14 \
    && rm -f /usr/local/bin/uv /usr/local/bin/uvx

COPY . /app
RUN mkdir -p /app/data \
    && chmod 755 /app/docker-entrypoint.sh \
    && addgroup -S -g 10001 app \
    && adduser -S -D -H -u 10001 -s /sbin/nologin -G app app \
    && chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
