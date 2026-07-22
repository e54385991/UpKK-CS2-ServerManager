"""Ownership and shutdown of process-local background tasks.

FastAPI route modules may create short-lived tasks, but the application
lifecycle owns their cleanup.  Keeping that ownership here avoids importing
HTTP routers from lifecycle code and gives tests an isolated registry type.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

TaskErrorHandler = Callable[[asyncio.Task[Any], BaseException], None]


class BackgroundTaskRegistry:
    """Keep strong task references and provide deterministic cancellation."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.tasks: set[asyncio.Task[Any]] = set()

    def add(
        self,
        task: asyncio.Task[Any],
        *,
        on_error: TaskErrorHandler | None = None,
    ) -> asyncio.Task[Any]:
        self.tasks.add(task)

        def task_done(completed: asyncio.Task[Any]) -> None:
            self.tasks.discard(completed)
            if completed.cancelled():
                return
            try:
                error = completed.exception()
            except asyncio.CancelledError:
                return
            if error is None:
                return
            if on_error is not None:
                on_error(completed, error)
            else:
                logger.error(
                    "Background task in %s failed",
                    self.name,
                    exc_info=error,
                )

        task.add_done_callback(task_done)
        return task

    def create(
        self,
        coroutine: Coroutine[Any, Any, Any],
        *,
        on_error: TaskErrorHandler | None = None,
    ) -> asyncio.Task[Any]:
        return self.add(asyncio.create_task(coroutine), on_error=on_error)

    async def shutdown(self) -> None:
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()


action_task_registry = BackgroundTaskRegistry("server actions")
file_task_registry = BackgroundTaskRegistry("file operations")
plugin_update_task_registry = BackgroundTaskRegistry("manual plugin updates")

TASK_REGISTRIES = (
    action_task_registry,
    file_task_registry,
    plugin_update_task_registry,
)


async def shutdown_background_tasks() -> None:
    """Cancel route-created work without importing route modules."""
    await asyncio.gather(
        *(registry.shutdown() for registry in TASK_REGISTRIES),
        return_exceptions=True,
    )
