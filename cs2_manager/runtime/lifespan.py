"""Canonical FastAPI lifespan entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def application_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start and stop the lifecycle owned by this exact app instance."""
    # Imported lazily while the legacy lifecycle is moved behind this package.
    # This avoids making the new core package depend on FastAPI route modules.
    from api.lifecycle import ApplicationLifecycle

    lifecycle = getattr(app.state, "lifecycle", None)
    if lifecycle is None:
        lifecycle = ApplicationLifecycle(container=app.state.container)
        app.state.lifecycle = lifecycle
    try:
        await lifecycle.start()
        yield
    finally:
        await lifecycle.stop()
