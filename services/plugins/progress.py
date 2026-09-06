"""Shared step-progress emitter for plugin and game-mode plan execution.

Lives outside ``plugin_conflict_service`` so the modules it calls into (the
panel-native framework installers) can report progress without importing the
planner back and creating a service import cycle.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

ProgressCallback = Callable[..., Awaitable[None]]


async def emit_plan_progress(
    progress: ProgressCallback | None,
    message: str,
    *,
    step_id: str,
    step_status: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    if progress is None:
        return
    event_metadata = {"step_id": step_id, "step_status": step_status}
    if metadata:
        event_metadata = {**event_metadata, **metadata}
    try:
        parameters = inspect.signature(progress).parameters.values()
        accepts_metadata = any(item.kind == inspect.Parameter.VAR_POSITIONAL for item in parameters)
        accepts_metadata = (
            accepts_metadata
            or sum(
                item.kind
                in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                for item in parameters
            )
            >= 3
        )
    except TypeError, ValueError:
        accepts_metadata = False
    if accepts_metadata:
        await progress(message, "status", event_metadata)
    else:
        await progress(message, "status")
