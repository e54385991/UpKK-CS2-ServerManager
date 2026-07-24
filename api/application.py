"""FastAPI application factory and component registration."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Mapping

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Lifespan

from api.metadata import APP_DESCRIPTION, APP_TITLE, APP_VERSION
from api.routes import (
    actions,
    auth,
    captcha,
    file_manager,
    github_plugins,
    gmail_oauth,
    health,
    map_management,
    pages,
    plugin_auto_update,
    plugin_configs,
    plugin_market,
    public,
    scheduled_tasks,
    server_status,
    servers,
    setup,
    system_settings,
)
from api.templating import STATIC_DIRECTORY, templates
from cs2_manager.core import (
    AppContainer,
    MetricsRegistry,
    RequestIDMiddleware,
    SettingsProtocol,
)
from cs2_manager.infrastructure import DatabaseResource, LegacyDatabaseResource
from cs2_manager.runtime import TaskSupervisor, application_lifespan
from modules.config import settings as default_settings
from modules.database import (
    get_db as legacy_get_db,
)
from services.maintenance_lock import (
    MAINTENANCE_LOCK_SERVICE_KEY,
    MaintenanceLockService,
    OperationBusyError,
    OperationCoordinationUnavailable,
    maintenance_lock_service,
)

# Ordering is intentional: unauthenticated endpoints are registered before the
# authenticated API routers, matching the legacy application contract.
API_ROUTERS = (
    public.router,
    captcha.router,
    auth.router,
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


async def operation_coordination_handler(
    _request: Request,
    exc: OperationCoordinationUnavailable,
) -> JSONResponse:
    """Fail closed when a destructive operation cannot obtain Redis coordination."""
    return JSONResponse(status_code=503, content={"detail": str(exc)})


def register_exception_handlers(app: FastAPI) -> None:
    """Register application-level exception mappings."""
    app.add_exception_handler(OperationBusyError, operation_busy_handler)
    app.add_exception_handler(
        OperationCoordinationUnavailable,
        operation_coordination_handler,
    )


def register_routes(app: FastAPI) -> None:
    """Register API and server-rendered page routers."""
    for router in API_ROUTERS:
        app.include_router(router)
    app.include_router(pages.router)
    app.include_router(health.router)


def _create_container(
    app_settings: SettingsProtocol,
    resource_overrides: Mapping[str, object] | None,
    metrics_registry: MetricsRegistry,
    *,
    isolated_defaults: bool,
) -> AppContainer:
    """Resolve resources without mutating any process-global registry."""
    overrides = dict(resource_overrides or {})
    overrides.pop("metrics", None)
    task_supervisor = overrides.pop("task_supervisor", None)
    if task_supervisor is None:
        task_supervisor = overrides.pop("tasks", None)
    if task_supervisor is None:
        task_supervisor = TaskSupervisor(
            "application",
            metrics_registry=metrics_registry,
        )
    services = overrides.pop("services", {})
    if not isinstance(services, Mapping):
        raise TypeError("resource_overrides['services'] must be a mapping")

    core_override_names = {"database", "redis", "http", "ssh_pool"}
    has_core_overrides = bool(core_override_names.intersection(overrides))
    if isolated_defaults:
        from modules.http_helper import HTTPHelper
        from services.redis_manager import RedisManager
        from services.ssh_connection_pool import SSHConnectionPool

        database = overrides.pop(
            "database",
            None,
        )
        if database is None:
            database = DatabaseResource.from_settings(app_settings)
        redis = overrides.pop("redis", None)
        if redis is None:
            redis = RedisManager(app_settings)
        http = overrides.pop("http", None)
        if http is None:
            http = HTTPHelper()
        if "ssh_pool" in overrides:
            ssh_pool = overrides.pop("ssh_pool")
        else:
            ssh_pool = SSHConnectionPool(shared=False)
    else:
        # The no-settings factory call remains the compatibility path used by
        # ``main:app``. Import these lazily so explicit settings never select
        # process-global resources merely by importing the application module.
        from modules.database import async_session_maker, engine
        from modules.http_helper import http_helper
        from services import redis_manager
        from services.ssh_connection_pool import ssh_connection_pool

        database = overrides.pop(
            "database",
            LegacyDatabaseResource(engine, async_session_maker),
        )
        redis = overrides.pop("redis", redis_manager)
        http = overrides.pop("http", http_helper)
        ssh_pool = overrides.pop("ssh_pool", ssh_connection_pool)

    legacy_runtime = not isolated_defaults and not has_core_overrides

    # Additional named resources are service adapters.  This makes the
    # boundary extensible without silently placing them on global modules.
    named_services = {**services, **overrides, "metrics": metrics_registry}
    from services.a2s_cache_service import (
        A2S_CACHE_SERVICE_KEY,
        A2SCacheService,
        a2s_cache_service,
    )
    from services.s3_backup_service import (
        S3_BACKUP_SERVICE_KEY,
        S3BackupService,
        s3_backup_service,
    )

    if A2S_CACHE_SERVICE_KEY not in named_services:
        named_services[A2S_CACHE_SERVICE_KEY] = (
            a2s_cache_service
            if legacy_runtime
            else A2SCacheService(
                redis_adapter=redis,  # type: ignore[arg-type]
                # Lightweight test/extension overrides historically only
                # supplied an opaque database marker. A real database resource
                # binds its factory here; incomplete compatibility doubles
                # retain the service's lazy legacy fallback.
                session_factory=getattr(database, "session_factory", None),
            )
        )
    if MAINTENANCE_LOCK_SERVICE_KEY not in named_services:
        named_services[MAINTENANCE_LOCK_SERVICE_KEY] = (
            maintenance_lock_service
            if legacy_runtime
            else MaintenanceLockService(redis_adapter=redis)  # type: ignore[arg-type]
        )
    if S3_BACKUP_SERVICE_KEY not in named_services:
        named_services[S3_BACKUP_SERVICE_KEY] = (
            s3_backup_service if legacy_runtime else S3BackupService()
        )
    return AppContainer(
        settings=app_settings,
        database=database,  # type: ignore[arg-type]
        redis=redis,  # type: ignore[arg-type]
        http=http,  # type: ignore[arg-type]
        ssh_pool=ssh_pool,  # type: ignore[arg-type]
        task_supervisor=task_supervisor,  # type: ignore[arg-type]
        services=named_services,
        overrides=resource_overrides or {},
        # The remaining background-service singletons still import legacy
        # process globals. Starting them beside an isolated database would
        # silently split one application across two engines, so only the exact
        # compatibility resource set may run them.
        legacy_runtime=legacy_runtime,
    )


def _database_dependency(
    container: AppContainer,
) -> Callable[[], AsyncGenerator[AsyncSession, None]]:
    """Bind request sessions to this app's container, not another factory."""

    async def get_container_db() -> AsyncGenerator[AsyncSession, None]:
        async with container.database.session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    return get_container_db


def create_app(
    *,
    settings: SettingsProtocol | None = None,
    resource_overrides: Mapping[str, object] | None = None,
    lifespan: Lifespan[FastAPI] | None = application_lifespan,
) -> FastAPI:
    """Create a FastAPI app with an isolated container and override registry."""
    isolated_defaults = settings is not None
    app_settings = settings if settings is not None else default_settings
    metrics_override = (resource_overrides or {}).get("metrics")
    if metrics_override is not None and not isinstance(metrics_override, MetricsRegistry):
        raise TypeError("resource_overrides['metrics'] must be a MetricsRegistry")
    metrics_registry = metrics_override or MetricsRegistry()
    container = _create_container(
        app_settings,
        resource_overrides,
        metrics_registry,
        isolated_defaults=isolated_defaults,
    )
    app = FastAPI(
        title=APP_TITLE,
        description=APP_DESCRIPTION,
        version=APP_VERSION,
        lifespan=lifespan,
    )
    app.state.container = container
    app.state.resources = container.resources
    app.state.settings = app_settings
    app.state.metrics = metrics_registry
    app.state.task_supervisor = container.task_supervisor
    app.state.templates = templates
    app.add_middleware(RequestIDMiddleware, metrics_registry=metrics_registry)
    app.dependency_overrides[legacy_get_db] = _database_dependency(container)

    register_exception_handlers(app)
    if STATIC_DIRECTORY.is_dir():
        app.mount(
            "/static",
            StaticFiles(directory=str(STATIC_DIRECTORY)),
            name="static",
        )
    register_routes(app)
    return app
