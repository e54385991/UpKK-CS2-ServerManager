"""Opt-in, cancellable retry budgets for background HTTP requests."""

from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

MAX_BACKGROUND_ATTEMPTS = 10


def retry_after_seconds(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except ValueError, TypeError, OverflowError:
            return 0.0
    return max(0.0, seconds) if math.isfinite(seconds) else 0.0


class RetryExhaustedError(RuntimeError):
    """A background request exhausted its finite attempt budget."""


@dataclass(frozen=True)
class BackgroundRetry:
    check: Callable[[], Awaitable[None]]
    notify: Callable[[int, float], Awaitable[None]]

    async def run[T](
        self,
        request: Callable[[], Awaitable[T]],
        retry_hint: Callable[[Exception], float | None],
    ) -> T:
        for attempt in range(1, MAX_BACKGROUND_ATTEMPTS + 1):
            await self.check()
            try:
                return await request()
            except Exception as exc:
                hint = retry_hint(exc)
                if hint is None:
                    raise
                if attempt == MAX_BACKGROUND_ATTEMPTS:
                    raise RetryExhaustedError(
                        f"Request failed after {MAX_BACKGROUND_ATTEMPTS} attempts; try again later"
                    ) from exc
                delay = max(hint, min(60.0, 2.0**attempt) + random.uniform(0, 1))
                await self.notify(attempt + 1, delay)
                while delay > 0:
                    step = min(delay, 30.0)
                    await asyncio.sleep(step)
                    delay -= step
                    await self.check()
        raise AssertionError("retry budget exhausted")
