"""Actions status endpoints."""

# ruff: noqa: F403,F405

from dataclasses import dataclass, field
from typing import Any

from api.dependencies import SSHManagerProvider, get_unit_of_work
from api.response_models import OperationMessageResponse, SSHConnectionInfoResponse
from cs2_manager.core import ErrorResponse, Principal
from cs2_manager.features.actions import (
    MetamodStatusService,
    ServerActionRepository,
)
from cs2_manager.features.actions import (
    ServerNotFoundError as ActionServerNotFoundError,
)
from cs2_manager.infrastructure import UnitOfWork
from modules import AuthType, MetamodStatusResponse
from modules.auth import get_current_principal

from .common import *

router = APIRouter(tags=["actions"])


@dataclass(frozen=True, slots=True)
class SSHServerSnapshot:
    """Connection fields detached from the request's database session."""

    id: int
    host: str
    ssh_port: int
    ssh_user: str
    auth_type: AuthType
    credential_revision: int
    ssh_password: str | None = field(repr=False)
    ssh_key_path: str | None
    ssh_host_key_algorithm: str | None
    ssh_host_key_fingerprint: str | None

    @property
    def is_password_auth(self) -> bool:
        return self.auth_type == AuthType.PASSWORD

    @property
    def is_key_auth(self) -> bool:
        return self.auth_type == AuthType.KEY_FILE


def _ssh_server_snapshot(server: Server) -> SSHServerSnapshot:
    if server.id is None:
        raise RuntimeError("Persisted server is missing its id")
    return SSHServerSnapshot(
        id=int(server.id),
        host=server.host,
        ssh_port=server.ssh_port,
        ssh_user=server.ssh_user,
        auth_type=server.auth_type,
        credential_revision=server.credential_revision,
        ssh_password=server.ssh_password,
        ssh_key_path=server.ssh_key_path,
        ssh_host_key_algorithm=server.ssh_host_key_algorithm,
        ssh_host_key_fingerprint=server.ssh_host_key_fingerprint,
    )


def _require_ssh_pool(request: Request, method_name: str) -> Any:
    """Resolve the current app's pool without falling back to process globals."""
    container = getattr(request.app.state, "container", None)
    pool = getattr(container, "ssh_pool", None)
    if pool is None or not callable(getattr(pool, method_name, None)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SSH connection pool is unavailable",
        )
    return pool


def _uow_session(uow: UnitOfWork) -> AsyncSession:
    if uow.session is None:
        raise RuntimeError("Unit of work is not active")
    return uow.session


def _require_metamod_cache(request: Request) -> Any:
    """Resolve the cache exclusively from the current application."""
    container = getattr(request.app.state, "container", None)
    cache = getattr(container, "redis", None)
    if cache is None or not all(callable(getattr(cache, name, None)) for name in ("get", "set")):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Metamod status cache is unavailable",
        )
    return cache


@router.get(
    "/servers/{server_id}/ssh-connection-info",
    response_model=SSHConnectionInfoResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def get_ssh_connection_info(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Get SSH connection information for a server.
    Returns connection status, age, reconnection count, and pooling status.
    """
    # Get server and verify ownership
    server = await get_server_and_verify_ownership(db, server_id, current_user)
    server_snapshot = _ssh_server_snapshot(server)

    # Ownership verification commits the read transaction before pool I/O.
    ssh_pool = _require_ssh_pool(request, "get_connection_info")
    connection_info = await ssh_pool.get_connection_info(server_snapshot)

    return connection_info


@router.post(
    "/servers/{server_id}/reconnect-ssh",
    response_model=OperationMessageResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def reconnect_ssh(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Manually reconnect SSH connection for a server.
    This bypasses rate limiting, resets the reconnection counter, and clears the SSH down flag.
    """
    from sqlalchemy import update as sql_update

    # Get server and verify ownership
    server = await get_server_and_verify_ownership(db, server_id, current_user)
    server_snapshot = _ssh_server_snapshot(server)
    ssh_pool = _require_ssh_pool(request, "manual_reconnect")

    # Clear the SSH down flag to allow reconnection
    if server.is_ssh_down:
        await db.execute(
            sql_update(Server)
            .where(Server.id == server_id)
            .values(is_ssh_down=False, consecutive_ssh_failures=0)
        )
        await db.commit()

    # Perform manual reconnection only after the DB transaction has ended.
    try:
        success, conn, msg = await ssh_pool.manual_reconnect(server_snapshot)
        if success:
            # Update ssh_health_status to healthy after successful reconnection
            now = get_current_time()
            await db.execute(
                sql_update(Server)
                .where(Server.id == server_id)
                .values(
                    ssh_health_status="healthy",
                    is_ssh_down=False,
                    consecutive_ssh_failures=0,
                    last_ssh_success=now,
                    last_ssh_health_check=now,
                )
            )
            await db.commit()

            return {"success": True, "message": msg}
        else:
            return {"success": False, "message": msg}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reconnect: {str(e)}",
        ) from e


@router.post(
    "/servers/{server_id}/reset-reconnect-counter",
    response_model=OperationMessageResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def reset_reconnect_counter(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Reset the reconnection counter for a server without reconnecting.
    """
    # Get server and verify ownership
    server = await get_server_and_verify_ownership(db, server_id, current_user)
    server_snapshot = _ssh_server_snapshot(server)

    # Ownership verification commits the read transaction before pool I/O.
    ssh_pool = _require_ssh_pool(request, "reset_reconnection_counter")

    try:
        await ssh_pool.reset_reconnection_counter(server_snapshot)
        return {"success": True, "message": "重连计数已重置 | Reconnection counter reset"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset counter: {str(e)}",
        ) from e


@router.get(
    "/servers/{server_id}/metamod-status",
    response_model=MetamodStatusResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def get_metamod_status(
    request: Request,
    server_id: int,
    ssh_manager: SSHManagerProvider,
    uow: UnitOfWork = Depends(get_unit_of_work),
    current_user: Principal = Depends(get_current_principal),
):
    """
    Check if Metamod:Source framework is installed on the server.
    Uses long-lived cache (1 hour) to avoid frequent SSH checks.

    Checks for the existence of:
    /cs2/game/csgo/addons/metamod/bin/linuxsteamrt64/metamod.2.cs2.so

    Returns:
        MetamodStatusResponse with installation status
    """
    cache = _require_metamod_cache(request)
    repository = ServerActionRepository(_uow_session(uow))
    try:
        target = await repository.require_metamod_target(server_id, current_user)
    except ActionServerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    await uow.commit()

    service = MetamodStatusService(
        cache,
        lambda: ssh_manager,
    )
    result = await service.get_status(target)
    return MetamodStatusResponse.model_validate(result.as_dict())
