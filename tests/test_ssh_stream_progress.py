"""Carriage-return SteamCMD progress must become live log lines."""

from __future__ import annotations

import pytest

from services.ssh.stream_progress import iter_ssh_progress_lines


class _ChunkStream:
    def __init__(self, chunks: list[str]):
        self._chunks = list(chunks)

    async def read(self, _size: int) -> str:
        if not self._chunks:
            return ""
        return self._chunks.pop(0)


class _LineStream:
    def __init__(self, lines: list[str]):
        self._lines = iter(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._lines)
        except StopIteration:
            raise StopAsyncIteration from None


@pytest.mark.asyncio
async def test_iter_ssh_progress_lines_splits_carriage_returns():
    stream = _ChunkStream(
        [
            "Waiting for user info...\r",
            " Update state (0x61) downloading, progress: 1.20 (12 / 1000)\r",
            " Update state (0x61) downloading, progress: 40.00 (400 / 1000)\r",
            "Success! App '730' fully installed.\n",
        ]
    )
    lines = [line async for line in iter_ssh_progress_lines(stream)]
    assert lines[0] == "Waiting for user info..."
    assert "1.20" in lines[1]
    assert "40.00" in lines[2]
    assert "fully installed" in lines[3]


@pytest.mark.asyncio
async def test_iter_ssh_progress_lines_falls_back_to_async_iteration():
    stream = _LineStream(["stdout-1\n", "stdout-2\n"])
    lines = [line async for line in iter_ssh_progress_lines(stream)]
    assert lines == ["stdout-1", "stdout-2"]
