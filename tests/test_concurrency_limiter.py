"""Regression coverage for bounded keyed asyncio concurrency state."""

from __future__ import annotations

import asyncio
from collections import Counter

import pytest

from services.concurrency_limiter import KeyedConcurrencyLimiter


def test_concurrency_limits_must_be_positive():
    with pytest.raises(ValueError, match="global_limit"):
        KeyedConcurrencyLimiter(global_limit=0, per_key_limit=1)
    with pytest.raises(ValueError, match="per_key_limit"):
        KeyedConcurrencyLimiter(global_limit=1, per_key_limit=0)


@pytest.mark.asyncio
async def test_limiter_enforces_both_limits_and_forgets_completed_keys():
    limiter = KeyedConcurrencyLimiter[str](global_limit=2, per_key_limit=1)
    release = asyncio.Event()
    first_user_started = asyncio.Event()
    other_user_started = asyncio.Event()
    active_by_key: Counter[str] = Counter()
    maximum_by_key: Counter[str] = Counter()
    active_total = 0
    maximum_total = 0

    async def worker(key: str, started: asyncio.Event | None = None) -> None:
        nonlocal active_total, maximum_total
        async with limiter.slot(key):
            active_total += 1
            active_by_key[key] += 1
            maximum_total = max(maximum_total, active_total)
            maximum_by_key[key] = max(maximum_by_key[key], active_by_key[key])
            if started is not None:
                started.set()
            await release.wait()
            active_by_key[key] -= 1
            active_total -= 1

    first = asyncio.create_task(worker("user-1", first_user_started))
    await first_user_started.wait()
    same_user = asyncio.create_task(worker("user-1"))
    other_user = asyncio.create_task(worker("user-2", other_user_started))
    await other_user_started.wait()

    assert maximum_total == 2
    assert maximum_by_key == {"user-1": 1, "user-2": 1}
    assert limiter.active_key_count == 2

    release.set()
    await asyncio.gather(first, same_user, other_user)

    assert maximum_total == 2
    assert maximum_by_key["user-1"] == 1
    assert limiter.active_key_count == 0


@pytest.mark.asyncio
async def test_cancelled_waiter_releases_its_key_entry():
    limiter = KeyedConcurrencyLimiter[str](global_limit=1, per_key_limit=1)
    release = asyncio.Event()
    holder_started = asyncio.Event()

    async def holder() -> None:
        async with limiter.slot("holder"):
            holder_started.set()
            await release.wait()

    async def waiter() -> None:
        async with limiter.slot("waiter"):
            raise AssertionError("cancelled waiter unexpectedly acquired a slot")

    holder_task = asyncio.create_task(holder())
    await holder_started.wait()
    waiter_task = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    assert limiter.active_key_count == 2

    waiter_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter_task
    assert limiter.active_key_count == 1

    release.set()
    await holder_task
    assert limiter.active_key_count == 0
