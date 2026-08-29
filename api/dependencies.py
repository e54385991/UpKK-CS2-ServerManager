"""Shared, typed FastAPI dependencies and authorization helpers."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from modules import (
    Server,
    User,
    get_current_active_user,
    get_current_admin_user,
    get_current_user,
    get_current_web_admin,
    get_current_web_user,
    get_db,
)
from modules.auth import WEB_SESSION_COOKIE, _get_active_user_for_token, optional_oauth2_scheme
from services.container import ServiceContainer
from services.maintenance_lock import maintenance_lock_service
from services.ssh_manager import SSHManager

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
ActiveUser = Annotated[User, Depends(get_current_active_user)]
AdminUser = Annotated[User, Depends(get_current_admin_user)]
WebUser = Annotated[User, Depends(get_current_web_user)]
WebAdmin = Annotated[User, Depends(get_current_web_admin)]


async def get_bearer_or_cookie_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(optional_oauth2_scheme)],
    db: DatabaseSession,
) -> User:
    """Authenticate EventSource/SSE with Bearer or the first-party session cookie.

    ``EventSource`` cannot set an ``Authorization`` header. The Next.js rewrite
    forwards the HttpOnly session cookie, so GET streams accept either.
    Mutations still require Bearer via ``ActiveUser``.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is not None:
        user = await _get_active_user_for_token(credentials.credentials, db)
        if user is None:
            raise credentials_exception
        return user

    token = request.cookies.get(WEB_SESSION_COOKIE)
    user = await _get_active_user_for_token(token, db) if token else None
    if user is None:
        raise credentials_exception
    return user


StreamUser = Annotated[User, Depends(get_bearer_or_cookie_user)]


def get_service_container(request: Request) -> ServiceContainer:
    """Return dependencies owned by the current application instance."""
    return request.app.state.services


ServiceDependencies = Annotated[ServiceContainer, Depends(get_service_container)]


def get_ssh_manager(services: ServiceDependencies) -> SSHManager:
    """Create an operation-scoped SSH facade that tests can override."""
    return services.ssh_manager_factory()


SSHManagerProvider = Annotated[SSHManager, Depends(get_ssh_manager)]


async def require_server_access(
    db: AsyncSession,
    server_id: int,
    user: User,
    *,
    commit: bool = True,
) -> Server:
    """Return an owned server while preserving the legacy 404 policy."""
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
):
    """Authorize and serialize a destructive server operation across processes."""
    server = await db.get(Server, server_id)
    if server is None or (not current_user.is_admin and server.user_id != current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    await db.commit()
    operation = f"{request.method}:{request.url.path}"
    async with maintenance_lock_service.get(
        server_id,
        operation=operation,
        wait=False,
        ttl=7200,
    ):
        yield server


LockedServerOperation = Annotated[Server, Depends(locked_server_operation)]
