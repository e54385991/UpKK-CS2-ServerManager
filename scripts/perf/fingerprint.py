"""Pin the starting SHA, lockfiles, machine, and harness versions."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts.perf.cases import measurement_matrix
from scripts.perf.env import (
    PERF_DATABASE,
    PERF_REDIS_PREFIX,
    IsolatedPorts,
    isolated_environ,
)
from scripts.perf.profiles import ARRIVAL_RATIO, MEASURES, ROUND_START_SHA
from scripts.perf.report import git_sha

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.perf.yml"


def file_digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _command_version(binary: str) -> str | None:
    executable = shutil.which(binary)
    if executable is None:
        return None
    result = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    line = (result.stdout or result.stderr).strip().splitlines()
    return line[0] if line else None


def memory_bytes() -> int | None:
    if sys.platform == "darwin":
        result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            check=False,
            capture_output=True,
            text=True,
        )
        raw = result.stdout.strip()
        return int(raw) if raw.isdigit() else None
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError, AttributeError):
        return None
    if page_size <= 0 or pages <= 0:
        return None
    return int(page_size) * int(pages)


def compose_images(text: str) -> list[str]:
    images: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("image:"):
            images.append(stripped.split(":", 1)[1].strip())
    return images


def isolated_postgres_ready(ports: IsolatedPorts) -> bool:
    """True only when the isolated user can open ``cs2_perf``."""
    try:
        import psycopg
    except ImportError:
        return False
    values = isolated_environ(ports)
    try:
        with psycopg.connect(
            host=values["POSTGRES_HOST"],
            port=values["POSTGRES_PORT"],
            user=values["POSTGRES_USER"],
            password=values["POSTGRES_PASSWORD"],
            dbname=values["POSTGRES_DATABASE"],
            connect_timeout=1,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database()")
                row = cursor.fetchone()
    except Exception:
        return False
    return bool(row and row[0] == PERF_DATABASE)


def isolated_redis_ready(ports: IsolatedPorts) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.2)
    try:
        probe.connect(("127.0.0.1", ports.redis))
        probe.sendall(b"PING\r\n")
        payload = probe.recv(16)
    except OSError:
        return False
    finally:
        probe.close()
    return payload.startswith(b"+PONG")


def loopback_bind(host: str, port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.2)
    try:
        probe.connect((host, port))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def capture_fingerprint(cwd: Path | None = None) -> dict[str, Any]:
    root = cwd or PROJECT_ROOT
    ports = IsolatedPorts()
    spec = MEASURES["baseline"]
    return {
        "round": "21197fe",
        "starting_sha": ROUND_START_SHA,
        "git_sha": git_sha(root),
        "matches_starting_sha": git_sha(root) == ROUND_START_SHA,
        "locks": {
            "uv.lock": file_digest(root / "uv.lock"),
            "frontend/package-lock.json": file_digest(root / "frontend" / "package-lock.json"),
        },
        "tools": {
            "python": sys.version.split()[0],
            "uv": _command_version("uv"),
            "node": _command_version("node"),
            "docker": _command_version("docker"),
        },
        "machine": {
            "platform": platform.platform(),
            "processor": platform.machine(),
            "cpus": os.cpu_count(),
            "memory_bytes": memory_bytes(),
            "hostname": platform.node(),
        },
        "isolated": {
            "postgres_host": "127.0.0.1",
            "postgres_port": ports.postgres,
            "postgres_database": PERF_DATABASE,
            "redis_host": "127.0.0.1",
            "redis_port": ports.redis,
            "redis_key_prefix": PERF_REDIS_PREFIX,
            "postgres_listening": loopback_bind("127.0.0.1", ports.postgres),
            "postgres_isolated": isolated_postgres_ready(ports),
            "redis_listening": loopback_bind("127.0.0.1", ports.redis),
            "redis_isolated": isolated_redis_ready(ports),
            "compose_images": compose_images(
                COMPOSE_FILE.read_text(encoding="utf-8") if COMPOSE_FILE.is_file() else ""
            ),
        },
        "protocol": {
            "arrival_ratio": ARRIVAL_RATIO,
            "api_warmup_seconds": spec.api_warmup_seconds,
            "api_measure_seconds": spec.api_measure_seconds,
            "api_rounds": spec.api_rounds,
            "browser_warmup": spec.browser_warmup,
            "browser_measure": spec.browser_measure,
            "browser_rounds": spec.browser_rounds,
            "soak_seconds": spec.soak_seconds,
            "claimed_gains": False,
        },
        "matrix": measurement_matrix(),
    }
