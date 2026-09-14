"""In-memory download chunk comparison. Does not change production chunk size."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from time import monotonic, perf_counter

from scripts.perf.profiles import CURRENT_DOWNLOAD_CHUNK, DOWNLOAD_CHUNK_CANDIDATES


class CountingWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.writes = 0
        self.bytes_written = 0
        self._handle = path.open("wb")

    def write(self, chunk: bytes) -> None:
        self.writes += 1
        self.bytes_written += len(chunk)
        self._handle.write(chunk)

    def close(self) -> None:
        self._handle.close()


async def copy_payload(payload: bytes, chunk_size: int, writer: CountingWriter) -> None:
    offset = 0
    while offset < len(payload):
        nxt = offset + chunk_size
        writer.write(payload[offset:nxt])
        offset = nxt
        await asyncio.sleep(0)


async def measure_cancel_ms(payload: bytes, chunk_size: int) -> float:
    started = monotonic()

    async def body() -> None:
        offset = 0
        while offset < len(payload):
            offset += chunk_size
            await asyncio.sleep(0)
            raise asyncio.CancelledError

    task = asyncio.create_task(body())
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return (monotonic() - started) * 1000


async def measure_chunk(
    payload: bytes,
    chunk_size: int,
    directory: Path,
) -> dict[str, float | int | bool]:
    target = directory / f"chunk-{chunk_size}.bin"
    writer = CountingWriter(target)
    started = perf_counter()
    await copy_payload(payload, chunk_size, writer)
    elapsed = perf_counter() - started
    writer.close()
    throughput = (len(payload) / elapsed) if elapsed > 0 else 0.0
    expected_writes = (len(payload) + chunk_size - 1) // chunk_size
    return {
        "chunk_size": chunk_size,
        "bytes": len(payload),
        "writes": writer.writes,
        "expected_writes": expected_writes,
        "elapsed_ms": round(elapsed * 1000, 3),
        "throughput_bps": round(throughput, 1),
        "cancel_ms": round(await measure_cancel_ms(payload, chunk_size), 3),
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
    return rows
