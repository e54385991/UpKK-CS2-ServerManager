"""Loopback-only process environment for the isolated performance stack."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

PERF_POSTGRES_PORT = "55432"
PERF_REDIS_PORT = "56379"
PERF_STUB_PORT = "18080"
PERF_API_PORT = "18000"
HOST_POSTGRES_PORT_KEY = "UPKK_PERF_POSTGRES_PORT"
HOST_REDIS_PORT_KEY = "UPKK_PERF_REDIS_PORT"
HOST_STUB_PORT_KEY = "UPKK_PERF_STUB_PORT"
HOST_API_PORT_KEY = "UPKK_PERF_API_PORT"
PERF_DATABASE = "cs2_perf"
PERF_REDIS_PREFIX = "perf:"
PERF_PASSWORD = "perf-isolated-pass-not-production"
REQUIRED_SECRET = "perf-isolated-secret-key-not-for-production-use"


@dataclass(frozen=True, slots=True)
class IsolatedPorts:
    postgres: int = int(PERF_POSTGRES_PORT)
    redis: int = int(PERF_REDIS_PORT)
    stub: int = int(PERF_STUB_PORT)
    api: int = int(PERF_API_PORT)


def _env_port(source: Mapping[str, str], key: str, default: str) -> int:
    raw = str(source.get(key) or "").strip() or default
    port = int(raw)
    if port <= 0 or port > 65535:
        raise ValueError(f"{key} must be a TCP port, got {raw!r}")
    return port


def ports_from_environ(source: Mapping[str, str] | None = None) -> IsolatedPorts:
    """Keep 55432/56379 unless this host already bound them to another project."""
    env = os.environ if source is None else source
    return IsolatedPorts(
        postgres=_env_port(env, HOST_POSTGRES_PORT_KEY, PERF_POSTGRES_PORT),
        redis=_env_port(env, HOST_REDIS_PORT_KEY, PERF_REDIS_PORT),
        stub=_env_port(env, HOST_STUB_PORT_KEY, PERF_STUB_PORT),
        api=_env_port(env, HOST_API_PORT_KEY, PERF_API_PORT),
    )


def isolated_environ(ports: IsolatedPorts | None = None) -> dict[str, str]:
    chosen = ports or ports_from_environ()
    return {
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": str(chosen.postgres),
        "POSTGRES_USER": "cs2_perf",
        "POSTGRES_PASSWORD": "cs2-perf-db-not-production",
        "POSTGRES_DATABASE": PERF_DATABASE,
        "REDIS_HOST": "127.0.0.1",
        "REDIS_PORT": str(chosen.redis),
        "REDIS_PASSWORD": "",
        "REDIS_DB": "0",
        "REDIS_KEY_PREFIX": PERF_REDIS_PREFIX,
        "REDIS_POOL_SIZE": "10",
        "REDIS_HEALTH_CHECK_INTERVAL": "30",
        "REDIS_SOCKET_CONNECT_TIMEOUT": "5",
        "REDIS_SOCKET_TIMEOUT": "5",
        "API_HOST": "127.0.0.1",
        "API_PORT": str(chosen.api),
        "DEBUG": "False",
        "RUN_MODE": "production",
        "BACKEND_URL": f"http://127.0.0.1:{chosen.api}",
        "LOG_LEVEL": "WARNING",
        "ASYNCSSH_LOG_LEVEL": "WARNING",
        "SECRET_KEY": REQUIRED_SECRET,
        "JWT_SECRET_KEY": REQUIRED_SECRET,
        "JWT_ALGORITHM": "HS256",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "10080",
        "SSH_AUTH_MODE": "password",
        "PERF_ISOLATED": "1",
        "PERF_STUB_ORIGIN": f"http://127.0.0.1:{chosen.stub}",
    }


def apply_isolated_env(
    environ: dict[str, str] | None = None,
    *,
    target: dict[str, str] | None = None,
) -> dict[str, str]:
    """Write isolated values before importing modules.config."""
    values = environ or isolated_environ()
    destination = os.environ if target is None else target
    for key, value in values.items():
        destination[key] = value
    return values


def assert_isolated_target(settings: Mapping[str, object] | object) -> None:
    """Refuse to seed or flush anything that is not the isolated database."""
    database = _setting(settings, "POSTGRES_DATABASE")
    prefix = _setting(settings, "REDIS_KEY_PREFIX")
    host = _setting(settings, "POSTGRES_HOST")
    if database != PERF_DATABASE:
        raise SystemExit(f"refusing non-isolated database {database!r}")
    if prefix != PERF_REDIS_PREFIX:
        raise SystemExit("refusing to continue without REDIS_KEY_PREFIX=perf:")
    if host not in {"127.0.0.1", "localhost"}:
        raise SystemExit(f"refusing non-loopback PostgreSQL host {host!r}")


def _setting(settings: Mapping[str, object] | object, name: str) -> str:
    if isinstance(settings, Mapping):
        return str(settings.get(name) or "")
    return str(getattr(settings, name, "") or "")
