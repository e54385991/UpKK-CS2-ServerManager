"""Typed callbacks for game-mode plan execution."""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

ProgressCallback = Callable[..., Awaitable[None]]
class PlanReport(Protocol):
    async def __call__(self, step_id: str, step_status: str, message: str, metadata: dict[str, Any] | None = None) -> None: ...
