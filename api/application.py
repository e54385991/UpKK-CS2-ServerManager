"""FastAPI application factory and component registration."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Lifespan

from api.lifecycle import application_lifespan
from api.metadata import APP_DESCRIPTION, APP_TITLE, APP_VERSION
from api.routes import (
    actions,
    ai,
    auth,
    captcha,
    discord_bot,
    file_manager,
    github_plugins,
    gmail_oauth,
    health,
    map_management,
    pages,
    plugin_auto_update,
    plugin_configs,
    plugin_diagnostics,
    plugin_market,
    public,
    scheduled_tasks,
    server_status,
    servers,
    setup,
    system_settings,
)
from api.templating import STATIC_DIRECTORY, templates
from services.container import ContainerFactory, build_service_container
from services.maintenance_lock import OperationBusyError

# Ordering is intentional: unauthenticated endpoints are registered before the
# authenticated API routers, matching the legacy application contract.
API_ROUTERS = (
    public.router,
    captcha.router,
    auth.router,
    discord_bot.router,
    ai.router,
    servers.router,
    actions.router,
    setup.router,
    server_status.router,
    file_manager.router,
    scheduled_tasks.router,
    github_plugins.router,
    plugin_market.router,
    plugin_auto_update.router,
    plugin_configs.router,
    plugin_diagnostics.router,
    system_settings.router,
    gmail_oauth.router,
    map_management.router,
)


async def operation_busy_handler(
    _request: Request,
    exc: OperationBusyError,
) -> JSONResponse:
    """Return a stable conflict response for distributed operation locks."""
    return JSONResponse(status_code=409, content={"detail": str(exc)})


def register_exception_handlers(app: FastAPI) -> None:
    """Register application-level exception mappings."""
    app.add_exception_handler(OperationBusyError, operation_busy_handler)


def register_routes(app: FastAPI) -> None:
    """Register API and server-rendered page routers."""
    for router in API_ROUTERS:
        app.include_router(router)
    app.include_router(pages.router)
    app.include_router(health.router)


def create_app(
    *,
    lifespan: Lifespan[FastAPI] | None = application_lifespan,
    container_factory: ContainerFactory = build_service_container,
) -> FastAPI:
    """Create and configure a FastAPI application instance."""
    app = FastAPI(
        title=APP_TITLE,
        description=APP_DESCRIPTION,
        version=APP_VERSION,
        lifespan=lifespan,
    )
    app.state.templates = templates
    app.state.container_factory = container_factory
    app.state.services = container_factory()

    register_exception_handlers(app)
    if STATIC_DIRECTORY.is_dir():
        app.mount(
            "/static",
            StaticFiles(directory=str(STATIC_DIRECTORY)),
            name="static",
        )
    register_routes(app)
    return app
