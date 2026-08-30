"""Split SSH process output on both newlines and carriage returns.

SteamCMD reprints download progress on the same line with ``\\r``. AsyncSSH's
default line iterator only yields on ``\\n``, so the panel looks frozen after
"Waiting for user info...OK" even though the download is moving.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


async def iter_ssh_progress_lines(stream: Any, *, chunk_size: int = 4096) -> AsyncIterator[str]:
    """Yield non-empty lines from an SSH stream, treating ``\\r`` as a line break."""
    read = getattr(stream, "read", None)
    if read is None:
        async for raw in stream:
            line = str(raw).rstrip("\n\r")
            if line:
                yield line
        return

    buffer = ""
    while True:
        chunk = await read(chunk_size)
        if not chunk:
            break
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", "replace")
        buffer += chunk
        while True:
            newline_at = buffer.find("\n")
            return_at = buffer.find("\r")
            if newline_at < 0 and return_at < 0:
                break
            if newline_at < 0:
                cut = return_at
            elif return_at < 0:
                cut = newline_at
            else:
                cut = min(newline_at, return_at)
            line = buffer[:cut].strip("\n\r")
            buffer = buffer[cut + 1 :]
            if line:
                yield line
    leftover = buffer.strip("\n\r")
    if leftover:
        yield leftover
