"""Low-frequency Linux host information collection with Redis caching."""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Sequence
from time import monotonic
from typing import TypedDict

from modules.models import Server
from modules.utils import get_current_time
from services import telemetry_runtime
from services.redis_manager import redis_manager
from services.ssh_manager import SSHManager

logger = logging.getLogger(__name__)

_CACHE_KEY_TEMPLATE = "host_system_info:{server_id}"
_LOCK_KEY_TEMPLATE = "host_system_info:lock:{server_id}"
_PROBE_TIMEOUT_SECONDS = 15
_LOCK_TTL_SECONDS = 60
_LOCK_WAIT_ATTEMPTS = 25
_LOCK_WAIT_SECONDS = 0.2


class HostSystemInfoData(TypedDict):
    """Serializable, non-secret host information kept in Redis."""

    server_id: int
    cached: bool
    success: bool
    system_type: str | None
    architecture: str | None
    cpu_model: str | None
    cpu_cores: int | None
    kernel_version: str | None
    distribution: str | None
    distribution_version: str | None
    distribution_pretty_name: str | None
    memory_total_bytes: int | None
    memory_available_bytes: int | None
    collected_at: str | None


def _cache_key(server_id: int) -> str:
    return _CACHE_KEY_TEMPLATE.format(server_id=server_id)


def _lock_key(server_id: int) -> str:
    return _LOCK_KEY_TEMPLATE.format(server_id=server_id)


def _clean_text(value: object, max_length: int = 255) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned[:max_length] or None


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(str(value).strip())
    except TypeError, ValueError:
        return None
    return parsed if parsed >= 0 else None


def _parse_probe_lines(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.isupper() and key.replace("_", "").isalnum():
            values[key] = value.strip()
    return values


def _snapshot_from_values(
    server_id: int,
    values: dict[str, str],
    collected_at: str | None,
) -> HostSystemInfoData:
    return {
        "server_id": server_id,
        "cached": False,
        "success": values.get("PROBE_OK") == "1",
        "system_type": _clean_text(values.get("SYSTEM_TYPE")),
        "architecture": _clean_text(values.get("ARCHITECTURE")),
        "cpu_model": _clean_text(values.get("CPU_MODEL")),
        "cpu_cores": _positive_int(values.get("CPU_CORES")),
        "kernel_version": _clean_text(values.get("KERNEL_VERSION")),
        "distribution": _clean_text(values.get("DISTRIBUTION"), 100),
        "distribution_version": _clean_text(values.get("DISTRIBUTION_VERSION"), 100),
        "distribution_pretty_name": _clean_text(values.get("DISTRIBUTION_PRETTY_NAME")),
        "memory_total_bytes": _positive_int(values.get("MEMORY_TOTAL_BYTES")),
        "memory_available_bytes": _positive_int(values.get("MEMORY_AVAILABLE_BYTES")),
        "collected_at": collected_at,
    }


def parse_host_system_info(
    output: str,
    server_id: int = 0,
    collected_at: str | None = None,
) -> HostSystemInfoData:
    """Parse the bounded key/value output returned by the remote probe."""
    return _snapshot_from_values(server_id, _parse_probe_lines(output), collected_at)


def _snapshot_from_cache(raw: object, server_id: int) -> HostSystemInfoData | None:
    if not isinstance(raw, dict):
        return None
    if _positive_int(raw.get("server_id")) != server_id:
        return None
    return {
        "server_id": server_id,
        "cached": True,
        "success": bool(raw.get("success")),
        "system_type": _clean_text(raw.get("system_type")),
        "architecture": _clean_text(raw.get("architecture")),
        "cpu_model": _clean_text(raw.get("cpu_model")),
        "cpu_cores": _positive_int(raw.get("cpu_cores")),
        "kernel_version": _clean_text(raw.get("kernel_version")),
        "distribution": _clean_text(raw.get("distribution"), 100),
        "distribution_version": _clean_text(raw.get("distribution_version"), 100),
        "distribution_pretty_name": _clean_text(raw.get("distribution_pretty_name")),
        "memory_total_bytes": _positive_int(raw.get("memory_total_bytes")),
        "memory_available_bytes": _positive_int(raw.get("memory_available_bytes")),
        "collected_at": _clean_text(raw.get("collected_at"), 64),
    }


def _mark_cached(snapshot: HostSystemInfoData, cached: bool) -> HostSystemInfoData:
    return {**snapshot, "cached": cached}


def _without_cached(snapshot: HostSystemInfoData) -> dict[str, object]:
    payload = dict(snapshot)
    payload["cached"] = False
    return payload


def _probe_command() -> str:
    """Return a shell-only, read-only probe with one SSH round trip."""
    return (
        "printf 'SYSTEM_TYPE=%s\\n' \"$(uname -s 2>/dev/null || true)\"; "
        "printf 'ARCHITECTURE=%s\\n' \"$(uname -m 2>/dev/null || true)\"; "
        "printf 'KERNEL_VERSION=%s\\n' \"$(uname -r 2>/dev/null || true)\"; "
        'cpu_model="$(LC_ALL=C lscpu 2>/dev/null | awk -F: \'$1 ~ /^Model name$/ {gsub(/^[ \\t]+/, "", $2); print $2; exit}\' || true)"; '
        'if [ -z "$cpu_model" ]; then cpu_model="$(awk -F: \'/^(model name|Hardware)/ {gsub(/^[ \\t]+/, "", $2); print $2; exit}\' /proc/cpuinfo 2>/dev/null || true)"; fi; '
        "printf 'CPU_MODEL=%s\\n' \"$cpu_model\"; "
        "printf 'CPU_CORES=%s\\n' \"$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || true)\"; "
        "printf 'DISTRIBUTION=%s\\n' \"$(sed -n 's/^ID=//p' /etc/os-release 2>/dev/null | head -n 1 | sed 's/^\"//;s/\"$//' || true)\"; "
        "printf 'DISTRIBUTION_VERSION=%s\\n' \"$(sed -n 's/^VERSION_ID=//p' /etc/os-release 2>/dev/null | head -n 1 | sed 's/^\"//;s/\"$//' || true)\"; "
        "printf 'DISTRIBUTION_PRETTY_NAME=%s\\n' \"$(sed -n 's/^PRETTY_NAME=//p' /etc/os-release 2>/dev/null | head -n 1 | sed 's/^\"//;s/\"$//' || true)\"; "
        "printf 'MEMORY_TOTAL_BYTES=%s\\n' \"$(awk '/^MemTotal:/ {print $2 * 1024; exit}' /proc/meminfo 2>/dev/null || true)\"; "
        "printf 'MEMORY_AVAILABLE_BYTES=%s\\n' \"$(awk '/^MemAvailable:/ {print $2 * 1024; exit}' /proc/meminfo 2>/dev/null || true)\"; "
        "printf 'PROBE_OK=1\\n'"
    )


class HostSystemInfoService:
    """Collect and cache host metadata without turning overview into polling."""

    CACHE_TTL_SECONDS = 15 * 60
    FAILURE_CACHE_TTL_SECONDS = 60

    async def get_host_system_info(
        self, server: Server, force_refresh: bool = False
    ) -> HostSystemInfoData:
        """Return one cached snapshot, refreshing only on a cache miss or explicit request."""
        server_id = int(server.id)
        if not force_refresh:
            cached = _snapshot_from_cache(await redis_manager.get(_cache_key(server_id)), server_id)
            if cached is not None:
                return _mark_cached(cached, True)

        # Admit before taking the distributed lock: queued work must not spend
        # the lock TTL waiting for another host's probe to finish.
        async with telemetry_runtime.ssh_probe_limiter.slot(
            (server.host.casefold(), server.ssh_port)
        ):
            return await self._refresh_host_system_info(server, force_refresh=force_refresh)

    async def _refresh_host_system_info(
        self, server: Server, *, force_refresh: bool
    ) -> HostSystemInfoData:
        server_id = int(server.id)
        token = secrets.token_urlsafe(18)
        lock_acquired = await redis_manager.acquire_lock(
            _lock_key(server_id), token, _LOCK_TTL_SECONDS
        )
        if lock_acquired is False:
            for _ in range(_LOCK_WAIT_ATTEMPTS):
                await asyncio.sleep(_LOCK_WAIT_SECONDS)
                cached = _snapshot_from_cache(
                    await redis_manager.get(_cache_key(server_id)), server_id
                )
                if cached is not None:
                    return _mark_cached(cached, True)
            return _snapshot_from_values(server_id, {}, None)

        try:
            if lock_acquired is True and not force_refresh:
                cached = _snapshot_from_cache(
                    await redis_manager.get(_cache_key(server_id)), server_id
                )
                if cached is not None:
                    return _mark_cached(cached, True)
            snapshot = await self._collect(server)
            await redis_manager.set(
                _cache_key(server_id),
                _without_cached(snapshot),
                expire=(
                    self.CACHE_TTL_SECONDS
                    if snapshot["success"]
                    else self.FAILURE_CACHE_TTL_SECONDS
                ),
            )
            return _mark_cached(snapshot, False)
        finally:
            if lock_acquired is True:
                await redis_manager.release_lock(_lock_key(server_id), token)

    async def get_many_host_system_info(
        self, servers: Sequence[Server], *, force_refresh: bool = False
    ) -> list[HostSystemInfoData]:
        """Read all snapshots together; existing locking still coordinates cache misses."""
        started = monotonic()
        raw = (
            [None] * len(servers)
            if force_refresh
            else await redis_manager.get_many([_cache_key(server.id) for server in servers])
        )
        cached = [
            _snapshot_from_cache(value, server.id)
            for server, value in zip(servers, raw, strict=True)
        ]

        async def read(server: Server, value: HostSystemInfoData | None) -> HostSystemInfoData:
            if value is not None:
                return value
            return await self.get_host_system_info(server, force_refresh=force_refresh)

        values = await telemetry_runtime.collect_ordered(
            read(server, value) for server, value in zip(servers, cached, strict=True)
        )
        telemetry_runtime.log_batch(
            logger,
            "host",
            started,
            count=len(servers),
            cache_hits=sum(value is not None for value in cached),
            failures=sum(not value["success"] for value in values),
        )
        return values

    async def _collect(self, server: Server) -> HostSystemInfoData:
        collected_at = get_current_time().isoformat()
        manager = SSHManager()
        try:
            connected, message = await manager.connect(server)
            if not connected:
                logger.warning(
                    "Host info SSH connection failed for server %s: %s", server.id, message
                )
                return _snapshot_from_values(int(server.id), {}, collected_at)
            success, stdout, stderr = await manager.execute_command(
                _probe_command(), timeout=_PROBE_TIMEOUT_SECONDS
            )
            if not success or not stdout.strip():
                logger.warning(
                    "Host info probe failed for server %s: %s",
                    server.id,
                    (stderr or "no output")[:200],
                )
                return _snapshot_from_values(int(server.id), {}, collected_at)
            snapshot = parse_host_system_info(stdout, int(server.id), collected_at)
            if not snapshot["success"]:
                logger.warning("Host info probe returned incomplete data for server %s", server.id)
            return snapshot
        except Exception as exc:
            logger.warning("Host info collection failed for server %s: %s", server.id, exc)
            return _snapshot_from_values(int(server.id), {}, collected_at)
        finally:
            await manager.disconnect()


host_system_info_service = HostSystemInfoService()

__all__ = [
    "HostSystemInfoData",
    "HostSystemInfoService",
    "host_system_info_service",
    "parse_host_system_info",
]
