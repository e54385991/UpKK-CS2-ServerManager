"""Preserve synchronous and asynchronous SSH text progress callbacks."""

import inspect
from collections.abc import Awaitable, Callable

TextProgress = Callable[[str], Awaitable[None] | None]


async def send_text_progress(callback: TextProgress | None, message: str) -> None:
    if callback:
        if inspect.iscoroutinefunction(callback):
            await callback(message)
        else:
            callback(message)
