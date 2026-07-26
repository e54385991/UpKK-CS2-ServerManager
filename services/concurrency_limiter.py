"""Reusable asyncio concurrency limits with bounded per-key state."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Generic, TypeVar

Key = TypeVar("Key")


@dataclass(slots=True)
class _SemaphoreEntry:
    semaphore: asyncio.Semaphore
    borrowers: int = 0


class KeyedConcurrencyLimiter(Generic[Key]):
    """Apply global and per-key limits without retaining inactive keys.

    A borrower is counted before it waits, so every task waiting on or holding a
    per-key semaphore shares the same entry.  The entry is removed when the last
    borrower exits, including when a waiting task is cancelled.
    """

    def __init__(self, *, global_limit: int, per_key_limit: int) -> None:
        if global_limit < 1:
            raise ValueError("global_limit must be at least 1")
        if per_key_limit < 1:
            raise ValueError("per_key_limit must be at least 1")

        self._global_semaphore = asyncio.Semaphore(global_limit)
        self._per_key_limit = per_key_limit
        self._entries: dict[Key, _SemaphoreEntry] = {}

    @property
    def active_key_count(self) -> int:
        """Return the number of keys with active or waiting borrowers."""
        return len(self._entries)

    @asynccontextmanager
    async def slot(self, key: Key) -> AsyncIterator[None]:
        """Wait for one per-key and global execution slot."""
        entry = self._entries.get(key)
        if entry is None:
            entry = _SemaphoreEntry(asyncio.Semaphore(self._per_key_limit))
            self._entries[key] = entry
        entry.borrowers += 1

        try:
            # Preserve the existing scheduling policy: a task first reserves
            # user capacity and then competes for global capacity.
            async with entry.semaphore:
                async with self._global_semaphore:
                    yield
        finally:
            entry.borrowers -= 1
            if entry.borrowers == 0 and self._entries.get(key) is entry:
                self._entries.pop(key, None)
