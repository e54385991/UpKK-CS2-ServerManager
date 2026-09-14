"""Download chunk comparison using the same await-per-chunk write loop as production.

This does not change ``DOWNLOAD_CHUNK_SIZE`` by itself. Adoption requires a 10%
throughput gain plus cancel, memory, and event-loop gates against 8 KiB.
"""

from __future__ import annotations

import asyncio
import tempfile
import tracemalloc
from contextlib import suppress
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any

import anyio

from scripts.perf.profiles import (
    CURRENT_DOWNLOAD_CHUNK,
    DOWNLOAD_CHUNK_CANDIDATES,
    DOWNLOAD_THROUGHPUT_GATE,
)
from scripts.perf.report import percentile_block, within_regression, within_resource_gate

PRODUCTION_CANDIDATE_CHUNK = 65536


class CountingWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.writes = 0
        self.bytes_written = 0
        self._handle: Any = None

    async def open(self) -> None:
        self._handle = await anyio.open_file(self.path, "wb")

    async def write(self, chunk: bytes) -> None:
        self.writes += 1
        self.bytes_written += len(chunk)
        await self._handle.write(chunk)

    async def aclose(self) -> None:
        if self._handle is not None:
            await self._handle.aclose()
            self._handle = None


async def copy_payload(payload: bytes, chunk_size: int, writer: CountingWriter) -> list[float]:
    loop_samples: list[float] = []
    offset = 0
    while offset < len(payload):
        nxt = offset + chunk_size
        await writer.write(payload[offset:nxt])
        offset = nxt
        started = perf_counter()
        await asyncio.sleep(0)
        loop_samples.append((perf_counter() - started) * 1000)
    return loop_samples


async def measure_cancel_ms(payload: bytes, chunk_size: int, directory: Path) -> float:
    writer = CountingWriter(directory / f"cancel-{chunk_size}.bin")
    await writer.open()
    started = monotonic()
    task = asyncio.create_task(copy_payload(payload, chunk_size, writer))
    await asyncio.sleep(0)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    await writer.aclose()
    return (monotonic() - started) * 1000


def throughput_gain(baseline_bps: float, candidate_bps: float) -> float:
    if baseline_bps <= 0:
        return 0.0
    return (candidate_bps - baseline_bps) / baseline_bps


def adopted_chunk_size(rows: list[dict[str, Any]]) -> int:
    """Return 64 KiB only when every Stage 5 gate passes; otherwise keep 8 KiB."""
    by_size = {int(row["chunk_size"]): row for row in rows}
    baseline = by_size.get(CURRENT_DOWNLOAD_CHUNK)
    candidate = by_size.get(PRODUCTION_CANDIDATE_CHUNK)
    if baseline is None or candidate is None:
        return CURRENT_DOWNLOAD_CHUNK
    if throughput_gain(float(baseline["throughput_bps"]), float(candidate["throughput_bps"])) < (
        DOWNLOAD_THROUGHPUT_GATE
    ):
        return CURRENT_DOWNLOAD_CHUNK
    if not within_regression(float(baseline["cancel_ms"]), float(candidate["cancel_ms"])):
        return CURRENT_DOWNLOAD_CHUNK
    if not within_resource_gate(float(baseline["peak_bytes"]), float(candidate["peak_bytes"])):
        return CURRENT_DOWNLOAD_CHUNK
    if not within_regression(float(baseline["loop_p95_ms"]), float(candidate["loop_p95_ms"])):
        return CURRENT_DOWNLOAD_CHUNK
    return PRODUCTION_CANDIDATE_CHUNK


async def measure_chunk(
    payload: bytes,
    chunk_size: int,
    directory: Path,
) -> dict[str, float | int | bool]:
    target = directory / f"chunk-{chunk_size}.bin"
    writer = CountingWriter(target)
    await writer.open()
    tracemalloc.start()
    started = perf_counter()
    loop_samples = await copy_payload(payload, chunk_size, writer)
    elapsed = perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    await writer.aclose()
    throughput = (len(payload) / elapsed) if elapsed > 0 else 0.0
    expected_writes = (len(payload) + chunk_size - 1) // chunk_size
    loop = percentile_block(loop_samples)
    return {
        "chunk_size": chunk_size,
        "bytes": len(payload),
        "writes": writer.writes,
        "expected_writes": expected_writes,
        "elapsed_ms": round(elapsed * 1000, 3),
        "throughput_bps": round(throughput, 1),
        "cancel_ms": round(await measure_cancel_ms(payload, chunk_size, directory), 3),
        "peak_bytes": peak,
        "loop_p95_ms": loop["p95"],
        "is_current_default": chunk_size == CURRENT_DOWNLOAD_CHUNK,
        "adopted": False,
    }


async def compare_chunks(
    payload_bytes: int = 4 * 1024 * 1024,
) -> list[dict[str, float | int | bool]]:
    payload = b"\xab" * payload_bytes
    rows: list[dict[str, float | int | bool]] = []
    with tempfile.TemporaryDirectory(prefix="upkk-perf-dl-") as raw:
        directory = Path(raw)
        for chunk_size in DOWNLOAD_CHUNK_CANDIDATES:
            rows.append(await measure_chunk(payload, chunk_size, directory))
    chosen = adopted_chunk_size(rows)
    for row in rows:
        row["adopted"] = bool(row["chunk_size"] == chosen and chosen != CURRENT_DOWNLOAD_CHUNK)
    return rows
