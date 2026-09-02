# Alpine keeps the production image small and avoids shipping Debian's
# perl/apt runtime packages.  The Python 3.14.7 wheels exported by uv include
# musllinux artifacts for every native dependency used by the application.
FROM python:3.14.7-alpine3.24@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBUG=False \
    RUN_MODE=production

WORKDIR /app

# Pull security fixes that may have landed after the Python base image was
# published without persisting repository indexes or download caches.
RUN apk upgrade --no-cache

# The production export is hash-pinned, so the image build is reproducible
# without installing the development toolchain or frontend dependencies.
COPY requirements.txt /tmp/requirements.txt
# pip is only a build-time tool; removing it also removes its vendored
# msgpack copy from the runtime vulnerability inventory.
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && rm -rf /usr/local/lib/python3.14/site-packages/pip \
        /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
        /usr/local/lib/python3.14/ensurepip \
        /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14

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
