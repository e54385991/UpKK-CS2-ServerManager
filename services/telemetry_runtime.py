"""Shared limits and cancellation ownership for read-only telemetry probes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Iterable
from time import monotonic

from services.concurrency_limiter import KeyedConcurrencyLimiter

MAX_CONCURRENT_SSH_PROBES = 4
ssh_probe_limiter = KeyedConcurrencyLimiter[tuple[str, int]](
    global_limit=MAX_CONCURRENT_SSH_PROBES, per_key_limit=1
)


async def collect_ordered[T](jobs: Iterable[Awaitable[T]]) -> list[T]:
    """Keep input order and drain every child before propagating failure/cancellation."""
    tasks = [asyncio.ensure_future(job) for job in jobs]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def log_batch(
    logger: logging.Logger,
    kind: str,
    started: float,
    *,
    count: int,
    cache_hits: int,
    failures: int,
) -> None:
    """Log bounded aggregate measurements, never hostnames or probe commands."""
    logger.debug(
        "Telemetry batch kind=%s count=%d cache_hits=%d failures=%d elapsed_ms=%.1f",
        kind,
        count,
        cache_hits,
        failures,
        (monotonic() - started) * 1000,
    )
