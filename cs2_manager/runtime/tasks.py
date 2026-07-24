"""Cancellation-safe ownership of background asyncio tasks."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any

from cs2_manager.core.metrics import MetricsRegistry

logger = logging.getLogger(__name__)

TaskErrorHandler = Callable[[asyncio.Task[Any], BaseException], None]


class TaskSupervisor:
    """Keep strong references and cancel all owned work during shutdown."""

    def __init__(
        self,
        name: str = "application",
        *,
        shutdown_timeout: float = 10.0,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None:
        self.name = name
        self.shutdown_timeout = shutdown_timeout
        self.metrics_registry = metrics_registry
        self.tasks: set[asyncio.Task[Any]] = set()
        self.failure_count = 0
        self._closing = False

    def start(self) -> None:
        """Allow a clean application instance to be started again in tests."""
        if self.tasks:
            raise RuntimeError("Cannot restart a task supervisor with live tasks")
        self._closing = False

    def add(
        self,
        task: asyncio.Task[Any],
        *,
        on_error: TaskErrorHandler | None = None,
    ) -> asyncio.Task[Any]:
        if self._closing:
            task.cancel()
            raise RuntimeError(f"Task supervisor {self.name!r} is shutting down")
        self.tasks.add(task)
        started_at = time.perf_counter()

        def completed(done: asyncio.Task[Any]) -> None:
            self.tasks.discard(done)
            if done.cancelled():
                self._observe_task(done, "cancelled", started_at)
                return
            try:
                error = done.exception()
            except asyncio.CancelledError:
                return
            if error is None:
                self._observe_task(done, "success", started_at)
                return
            self.failure_count += 1
            self._observe_task(done, "error", started_at)
            if on_error is not None:
                on_error(done, error)
                return
            logger.error(
                "Background task %s in %s failed",
                done.get_name(),
                self.name,
                exc_info=(type(error), error, error.__traceback__),
            )

        task.add_done_callback(completed)
        return task

    def _observe_task(
        self,
        task: asyncio.Task[Any],
        outcome: str,
        started_at: float,
    ) -> None:
        if self.metrics_registry is None:
            return
        self.metrics_registry.observe_background_task(
            name=task.get_name(),
            outcome=outcome,
            duration_seconds=time.perf_counter() - started_at,
        )

    def create(
        self,
        coroutine: Coroutine[Any, Any, Any],
        *,
        name: str | None = None,
        on_error: TaskErrorHandler | None = None,
    ) -> asyncio.Task[Any]:
        """Create and own a task; callers pass IDs/data rather than ORM sessions."""
        if self._closing:
            coroutine.close()
            raise RuntimeError(f"Task supervisor {self.name!r} is shutting down")
        task = asyncio.create_task(coroutine, name=name)
        return self.add(task, on_error=on_error)

    spawn = create

    async def shutdown(self) -> None:
        """Cancel all current work and wait for cancellation cleanup."""
        self._closing = True
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                async with asyncio.timeout(self.shutdown_timeout):
                    await asyncio.gather(*tasks, return_exceptions=True)
            except TimeoutError:
                logger.error(
                    "Timed out waiting for %s background task(s) in %s",
                    len(self.tasks),
                    self.name,
                )
        self.tasks.intersection_update(task for task in tasks if not task.done())
