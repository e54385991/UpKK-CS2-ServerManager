"""Versioned host auto-setup and saved-initialized-host list for the Next console."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from api.dependencies import ActiveUser, DatabaseSession, StreamUser
from api.routes.servers.crud import create_server_record
from api.routes.setup import ServerSetupRequest, auto_setup_server, generate_secure_password
from modules import ServerCreate
from modules.database import async_session_maker
from services.initialized_server_service import (
    InitializedServerAccessDenied,
    InitializedServerRecord,
    resolve_initialized_server,
)
from services.initialized_server_service import (
    delete_initialized_server as delete_saved_initialized_server,
)
from services.initialized_server_service import (
    delete_initialized_servers as delete_saved_initialized_servers,
)
from services.initialized_server_service import (
    list_initialized_servers as list_saved_initialized_servers,
)
from services.redis_manager import redis_manager
from services.server_operation_hub import (
    ACTIVE_STATUSES,
    ServerOperationConflict,
    server_operation_hub,
)
from services.server_setup_script import build_manual_setup_script, validate_cs2_username

from .operation_runner import enqueue_initialized_host_ssh_test, enqueue_server_operation
from .operations import _operation_event_source, to_view
from .schemas import (
    ActionResult,
    AutoSetupRequest,
    AutoSetupResultView,
    InitializedHostBatchDeleteRequest,
    InitializedHostCredentialsView,
    InitializedHostDeployRequest,
    InitializedHostDeployView,
    InitializedHostOperationRequest,
    InitializedHostOperationView,
    InitializedHostView,
    ManualSetupScriptView,
)

router = APIRouter(prefix="/api/v1/setup", tags=["v1-setup"])


def _to_list_item(raw: InitializedServerRecord) -> InitializedHostView:
    return InitializedHostView(
        key=raw.key,
        name=raw.name,
        host=raw.host,
        ssh_port=raw.ssh_port,
        ssh_user=raw.ssh_user,
        game_directory=raw.game_directory,
        created_at=raw.created_at,
    )


def _to_credentials(raw: InitializedServerRecord) -> InitializedHostCredentialsView:
    return InitializedHostCredentialsView(
        key=raw.key,
        name=raw.name,
        host=raw.host,
        ssh_port=raw.ssh_port,
        ssh_user=raw.ssh_user,
        ssh_password=raw.ssh_password,
        game_directory=raw.game_directory,
        created_at=raw.created_at,
    )


async def _resolve_owned(db, key: str, user_id: int) -> InitializedServerRecord:
    try:
        resolved = await resolve_initialized_server(db, key, user_id, legacy_store=redis_manager)
    except InitializedServerAccessDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this server configuration",
        ) from exc
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Initialized server not found",
        )
    return resolved.record


def _host_operation_view(record: dict, initialized_server_id: int) -> InitializedHostOperationView:
    operation_id = str(record["operation_id"])
    return InitializedHostOperationView(
        operation_id=operation_id,
        initialized_server_id=initialized_server_id,
        action="test_ssh",
        status=record["status"],
        success=record.get("success"),
        message=record.get("message"),
        started_at=record["started_at"],
        completed_at=record.get("completed_at"),
        actor_user_id=int(record["actor_user_id"]),
        stream_url=(
            f"/api/v1/setup/initialized-servers/{initialized_server_id}/operations/"
            f"{operation_id}/events"
        ),
        command=str(record["command"]) if record.get("command") else None,
    )


@router.get("/initialized-servers", response_model=list[InitializedHostView])
async def list_initialized_hosts(
    db: DatabaseSession,
    current_user: ActiveUser,
) -> list[InitializedHostView]:
    servers = await list_saved_initialized_servers(db, current_user.id, legacy_store=redis_manager)
    return [_to_list_item(item) for item in servers]


@router.post(
    "/initialized-servers/batch-delete",
    response_model=ActionResult,
)
async def batch_delete_initialized_hosts(
    body: InitializedHostBatchDeleteRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ActionResult:
    deleted = await delete_saved_initialized_servers(db, body.ids, current_user.id)
    return ActionResult(success=True, message=f"Deleted {deleted} initialized host(s)")


@router.post(
    "/initialized-servers/{initialized_server_id}/operations",
    response_model=InitializedHostOperationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_initialized_host_operation(
    initialized_server_id: int,
    body: InitializedHostOperationRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> InitializedHostOperationView:
    await _resolve_owned(db, str(initialized_server_id), current_user.id)
    try:
        record = await enqueue_initialized_host_ssh_test(
            initialized_server_id=initialized_server_id,
            actor_user_id=current_user.id,
        )
    except ServerOperationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _host_operation_view(record, initialized_server_id)


@router.get(
    "/initialized-servers/{initialized_server_id}/operations/current",
    response_model=InitializedHostOperationView | None,
)
async def get_current_initialized_host_operation(
    initialized_server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> InitializedHostOperationView | None:
    await _resolve_owned(db, str(initialized_server_id), current_user.id)
    record = await server_operation_hub.get_current(-initialized_server_id)
    if record is None:
        return None
    if int(record.get("server_id") or 0) != -initialized_server_id:
        return None
    return _host_operation_view(record, initialized_server_id)


@router.get(
    "/initialized-servers/{initialized_server_id}/operations/{operation_id}",
    response_model=InitializedHostOperationView,
)
async def get_initialized_host_operation(
    initialized_server_id: int,
    operation_id: UUID,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> InitializedHostOperationView:
    await _resolve_owned(db, str(initialized_server_id), current_user.id)
    record = await server_operation_hub.get(str(operation_id))
    if record is None or int(record.get("server_id") or 0) != -initialized_server_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    return _host_operation_view(record, initialized_server_id)


@router.post(
    "/initialized-servers/{initialized_server_id}/operations/{operation_id}/cancel",
    response_model=InitializedHostOperationView,
)
async def cancel_initialized_host_operation(
    initialized_server_id: int,
    operation_id: UUID,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> InitializedHostOperationView:
    """Force-stop a queued or running initialized-host SSH test."""
    await _resolve_owned(db, str(initialized_server_id), current_user.id)
    op_id = str(operation_id)
    record = await server_operation_hub.get(op_id)
    if record is None or int(record.get("server_id") or 0) != -initialized_server_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    if record.get("status") not in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Operation is no longer queued or running",
        )
    cancelled = await server_operation_hub.cancel(
        op_id,
        message="Operation force-stopped by operator",
    )
    if cancelled is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    return _host_operation_view(cancelled, initialized_server_id)


@router.get(
    "/initialized-servers/{initialized_server_id}/operations/{operation_id}/events",
    response_model=None,
)
async def stream_initialized_host_operation_events(
    initialized_server_id: int,
    operation_id: UUID,
    request: Request,
    current_user: StreamUser,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    async with async_session_maker() as db:
        await _resolve_owned(db, str(initialized_server_id), current_user.id)
    record = await server_operation_hub.get(str(operation_id))
    if record is None or int(record.get("server_id") or 0) != -initialized_server_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    return StreamingResponse(
        _operation_event_source(request, str(operation_id), after),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/initialized-servers/{initialized_server_id}/deploy",
    response_model=InitializedHostDeployView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def deploy_from_initialized_host(
    initialized_server_id: int,
    body: InitializedHostDeployRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
) -> InitializedHostDeployView:
    saved = await _resolve_owned(db, str(initialized_server_id), current_user.id)
    server_data = ServerCreate.model_validate(
        {
            "name": body.name,
            "host": saved.host,
            "ssh_port": saved.ssh_port,
            "ssh_user": saved.ssh_user,
            "ssh_password": saved.ssh_password,
            "game_port": body.game_port,
            "game_directory": saved.game_directory,
            "server_name": body.server_name,
            "captcha_token": body.captcha_token,
            "captcha_code": body.captcha_code,
        }
    )
    server = await create_server_record(
        server_data,
        db,
        current_user,
        request,
        skip_host_initialization=True,
    )
    try:
        operation = await enqueue_server_operation(
            server_id=server.id,
            action="deploy",
            actor_user_id=current_user.id,
        )
    except ServerOperationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return InitializedHostDeployView(
        initialized_server_id=initialized_server_id,
        server_id=server.id,
        operation=to_view(operation),
    )


@router.get(
    "/initialized-servers/{server_key:path}/credentials",
    response_model=InitializedHostCredentialsView,
)
async def read_initialized_host_credentials(
    server_key: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> InitializedHostCredentialsView:
    return _to_credentials(await _resolve_owned(db, server_key, current_user.id))


@router.delete("/initialized-servers/{server_key:path}", response_model=ActionResult)
async def delete_initialized_host(
    server_key: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ActionResult:
    await _resolve_owned(db, server_key, current_user.id)
    try:
        success = await delete_saved_initialized_server(
            db,
            server_key,
            current_user.id,
            legacy_store=redis_manager,
        )
    except InitializedServerAccessDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this server configuration",
        ) from exc
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete server configuration",
        )
    return ActionResult(success=True, message="Initialized server deleted successfully")


@router.get("/manual-script", response_model=ManualSetupScriptView)
async def read_manual_setup_script(
    current_user: ActiveUser,
    cs2_username: str = Query(default="cs2server"),
) -> ManualSetupScriptView:
    del current_user
    try:
        username = validate_cs2_username(cs2_username)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    password = generate_secure_password()
    return ManualSetupScriptView(
        cs2_username=username,
        password=password,
        script=build_manual_setup_script(cs2_username=username, password=password),
    )


@router.post("/auto-setup", response_model=AutoSetupResultView)
async def run_auto_setup(
    body: AutoSetupRequest,
    current_user: ActiveUser,
    db: DatabaseSession,
) -> AutoSetupResultView:
    """Same Linux-user + apt path as Jinja ``/api/setup/auto-setup``."""
    result = await auto_setup_server(
        ServerSetupRequest(
            name=body.name,
            host=body.host,
            ssh_port=body.ssh_port,
            ssh_user=body.ssh_user,
            ssh_password=body.ssh_password,
            sudo_password=body.sudo_password,
            cs2_username=body.cs2_username,
            cs2_password=body.cs2_password,
            captcha_token=body.captcha_token,
            captcha_code=body.captcha_code,
            save_config=body.save_config,
            open_game_ports=body.open_game_ports,
            session_id=body.session_id,
        ),
        current_user,
        db,
    )
    return AutoSetupResultView(
        success=result.success,
        message=result.message,
        cs2_username=result.cs2_username,
        cs2_password=result.cs2_password,
        game_directory=result.game_directory,
        logs=list(result.logs or []),
        initialized_server_id=result.initialized_server_id,
    )
