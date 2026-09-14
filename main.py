"""ASGI entry point for CS2 Server Manager.

The application is assembled in :mod:`api.application`; this module intentionally
keeps the long-standing ``main:app`` deployment contract and compatibility
exports used by integrations and tests.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.application import create_app, operation_busy_handler
from api.lifecycle import start_application, stop_application
from api.routes.health import health_check
from modules import _get_log_level, settings, setup_logging

__all__ = [
    "app",
    "health_check",
    "lifespan",
    "operation_busy_handler",
    "shutdown_event",
    "startup_event",
]


# Initialize logging once at process import, before the ASGI server starts.
setup_logging(
    level=_get_log_level(settings.LOG_LEVEL),
    asyncssh_level=settings.ASYNCSSH_LOG_LEVEL,
)


async def startup_event() -> None:
    """Compatibility wrapper for application startup."""
    await start_application()


async def shutdown_event() -> None:
    """Compatibility wrapper for application shutdown."""
    await stop_application()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run startup and guarantee cleanup after startup or runtime failures."""
    try:
        await startup_event()
        yield
    finally:
        await shutdown_event()


app = create_app(lifespan=lifespan)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG,
    )
