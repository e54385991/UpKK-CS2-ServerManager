# Pin the latest stable Python 3.15 patch release and its multi-arch digest.
# Native extensions without cp315 musllinux wheels build in a separate stage.
FROM ghcr.io/astral-sh/uv:0.13.4-alpine@sha256:325c1a47db636422f3d1e6a935a0a9d0e6395031665e5baa033d1691ddc9c27e AS uv
FROM python:3.15.0-alpine3.24@sha256:2699b90f4d562a22e2dd076c82f964032f64814204bff36d271a54ba4cc2c7e2 AS python

RUN apk upgrade --no-cache \
    && apk add --no-cache libstdc++

FROM python AS dependencies

ENV UV_LINK_MODE=copy

RUN apk add --no-cache build-base linux-headers
COPY --from=uv /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/
COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv --python /usr/local/bin/python /opt/venv \
    && uv pip install --python /opt/venv/bin/python --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

FROM python AS runtime

ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    DEBUG=False \
    RUN_MODE=production \
    APP_GIT_SHA=${GIT_SHA} \
    APP_BUILD_TIME=${BUILD_TIME}

WORKDIR /app

COPY --from=dependencies /opt/venv /opt/venv
# Resolve base-interpreter paths rather than hard-coding its minor version.
# The uv-created virtual environment has no pip; remove the base copy too.
RUN /usr/local/bin/python - <<'PY'
import shutil
import sys
import sysconfig
from pathlib import Path

site = Path(sysconfig.get_path("purelib"))
for path in (site / "pip", *site.glob("pip-*.dist-info")):
    if path.is_dir():
        shutil.rmtree(path)
shutil.rmtree(Path(sysconfig.get_path("stdlib")) / "ensurepip")
scripts = Path(sysconfig.get_path("scripts"))
for name in ("pip", "pip3", f"pip{sys.version_info.major}.{sys.version_info.minor}"):
    (scripts / name).unlink(missing_ok=True)
PY

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
