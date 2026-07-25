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
from api.routes.pages import (
    console_popup,
    deployment_tutorial_page,
    file_editor_popup,
    forgot_password_page,
    game_console,
    google_callback_page,
    login_page,
    plugin_market_page,
    profile_page,
    register_page,
    reset_password_page,
    root,
    server_detail_ui,
    servers_ui,
    setup_wizard,
    ssh_console,
    system_settings_page,
)
from api.templating import STATIC_ASSET_VERSION, static_url, templates
from modules import _get_log_level, settings, setup_logging

__all__ = [
    "STATIC_ASSET_VERSION",
    "app",
    "console_popup",
    "deployment_tutorial_page",
    "file_editor_popup",
    "forgot_password_page",
    "game_console",
    "google_callback_page",
    "health_check",
    "lifespan",
    "login_page",
    "operation_busy_handler",
    "plugin_market_page",
    "profile_page",
    "register_page",
    "reset_password_page",
    "root",
    "server_detail_ui",
    "servers_ui",
    "setup_wizard",
    "shutdown_event",
    "ssh_console",
    "startup_event",
    "static_url",
    "system_settings_page",
    "templates",
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
