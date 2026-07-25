"""Shared, typed FastAPI dependencies and authorization helpers."""

from collections.abc import AsyncIterator, Callable
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from api.http_resource import as_application_http
from cs2_manager.core import Principal
from cs2_manager.infrastructure import UnitOfWork
from modules import (
    Server,
    User,
    get_current_active_user,
    get_current_web_admin,
    get_current_web_user,
    get_db,
)
from modules.auth import get_current_principal
from services.maintenance_lock import (
    MAINTENANCE_LOCK_SERVICE_KEY,
    MaintenanceLockService,
    OperationCoordinationUnavailable,
)
from services.ssh_manager import SSHManager

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
ActiveUser = Annotated[User, Depends(get_current_active_user)]
WebUser = Annotated[User, Depends(get_current_web_user)]
WebAdmin = Annotated[User, Depends(get_current_web_admin)]


def get_ssh_manager(connection: HTTPConnection) -> SSHManager:
    """Create an operation-scoped SSH facade from this application's resources."""
    container = getattr(connection.app.state, "container", None)
    connection_pool = getattr(container, "ssh_pool", None)
    if connection_pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SSH connection pool is unavailable",
        )
    http_resource = as_application_http(getattr(container, "http", None))
    if http_resource is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Outbound HTTP client is unavailable",
        )
    if not callable(getattr(http_resource, "download_file", None)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Outbound HTTP client is unavailable",
        )
    return SSHManager(
        connection_pool=connection_pool,
        http_resource=http_resource,
    )


SSHManagerProvider = Annotated[SSHManager, Depends(get_ssh_manager)]


async def get_admin_principal(
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    """Require an administrator without retaining an ORM user/session."""
    if not principal.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions",
        )
    return principal


async def get_unit_of_work(request: Request) -> AsyncIterator[UnitOfWork]:
    """Yield a UnitOfWork owned by the current application."""
    container = getattr(request.app.state, "container", None)
    database = getattr(container, "database", None)
    unit_of_work = cast(
        Callable[[], UnitOfWork] | None,
        getattr(database, "unit_of_work", None),
    )
    if not callable(unit_of_work):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unit of work is unavailable",
        )
    async with unit_of_work() as uow:
        yield uow


def resolve_maintenance_lock_service(request: Request) -> MaintenanceLockService:
    """Resolve only the lock service owned by the current application."""
    container = getattr(request.app.state, "container", None)
    services = getattr(container, "services", {})
    service = services.get(MAINTENANCE_LOCK_SERVICE_KEY)
    if service is None or not callable(getattr(service, "get", None)):
        raise OperationCoordinationUnavailable(
            "Operation coordination is unavailable; refusing destructive operation"
        )
    return cast(MaintenanceLockService, service)


def resolve_a2s_cache_service(request: Request | None = None):
    """Resolve the A2S service owned by the request's application."""
    from services.a2s_cache_service import (
        A2S_CACHE_SERVICE_KEY,
        A2SCacheService,
        a2s_cache_service,
    )

    if request is None:
        # Non-ASGI compatibility callers may still opt into the historical
        # singleton explicitly. Request handling never takes this branch.
        return a2s_cache_service

    container = getattr(request.app.state, "container", None)
    services = getattr(container, "services", {})
    service = services.get(A2S_CACHE_SERVICE_KEY)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A2S cache service is unavailable",
        )
    return cast(A2SCacheService, service)


def resolve_s3_backup_service(request: Request):
    """Resolve only the S3 cache/service owned by the current application."""
    from services.s3_backup_service import (
        S3_BACKUP_SERVICE_KEY,
        S3BackupService,
    )

    container = getattr(request.app.state, "container", None)
    services = getattr(container, "services", {})
    service = services.get(S3_BACKUP_SERVICE_KEY)
    if service is None or not callable(getattr(service, "close", None)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="S3 backup service is unavailable",
        )
    return cast(S3BackupService, service)


async def require_server_access(
    db: AsyncSession,
    server_id: int,
    user: User,
    *,
    commit: bool = True,
) -> Server:
    """Return an owned server while preserving the legacy 404 policy."""
    if user.id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found",
        )
    if user.is_admin:
        server = await Server.get_by_id(db, server_id)
    else:
        server = await Server.get_by_id_and_user(db, server_id, user.id)

    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found",
        )
    if commit:
        await db.commit()
    return server


async def locked_server_operation(
    request: Request,
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
    lock_service: MaintenanceLockService = Depends(resolve_maintenance_lock_service),
):
    """Authorize and serialize a destructive server operation across processes."""
    server = await db.get(Server, server_id)
    if server is None or (not current_user.is_admin and server.user_id != current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    await db.commit()
    operation = f"{request.method}:{request.url.path}"
    async with lock_service.get(
        server_id,
        operation=operation,
        wait=False,
        ttl=7200,
    ):
        yield server
