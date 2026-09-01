"""Versioned host auto-setup and saved-initialized-host list for the Next console."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from api.dependencies import ActiveUser, DatabaseSession
from api.routes.setup import ServerSetupRequest, auto_setup_server, generate_secure_password
from services.redis_manager import redis_manager
from services.server_setup_script import build_manual_setup_script, validate_cs2_username

from .schemas import (
    ActionResult,
    AutoSetupRequest,
    AutoSetupResultView,
    InitializedHostCredentialsView,
    InitializedHostView,
    ManualSetupScriptView,
)

router = APIRouter(prefix="/api/v1/setup", tags=["v1-setup"])


def _to_list_item(raw: dict) -> InitializedHostView:
    return InitializedHostView(
        key=str(raw.get("key") or ""),
        name=str(raw.get("name") or ""),
        host=str(raw.get("host") or ""),
        ssh_port=int(raw.get("ssh_port") or 22),
        ssh_user=str(raw.get("ssh_user") or ""),
        game_directory=str(raw.get("game_directory") or ""),
        created_at=float(raw.get("created_at") or 0),
    )


def _require_owned_initialized(server_data: dict | None, user_id: int) -> dict:
    if not server_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Initialized server not found or already expired",
        )
    if server_data.get("user_id") != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this server configuration",
        )
    return server_data


@router.get("/initialized-servers", response_model=list[InitializedHostView])
async def list_initialized_hosts(current_user: ActiveUser) -> list[InitializedHostView]:
    servers = await redis_manager.get_initialized_servers(current_user.id)
    return [_to_list_item(item) for item in servers]


@router.get(
    "/initialized-servers/{server_key:path}/credentials",
    response_model=InitializedHostCredentialsView,
)
async def read_initialized_host_credentials(
    server_key: str,
    current_user: ActiveUser,
) -> InitializedHostCredentialsView:
    raw = _require_owned_initialized(
        await redis_manager.get_initialized_server(server_key),
        current_user.id,
    )
    return InitializedHostCredentialsView(
        key=str(raw.get("key") or server_key),
        name=str(raw.get("name") or ""),
        host=str(raw.get("host") or ""),
        ssh_port=int(raw.get("ssh_port") or 22),
        ssh_user=str(raw.get("ssh_user") or ""),
        ssh_password=str(raw.get("ssh_password") or ""),
        game_directory=str(raw.get("game_directory") or ""),
        created_at=float(raw.get("created_at") or 0),
    )


@router.delete("/initialized-servers/{server_key:path}", response_model=ActionResult)
async def delete_initialized_host(server_key: str, current_user: ActiveUser) -> ActionResult:
    _require_owned_initialized(
        await redis_manager.get_initialized_server(server_key),
        current_user.id,
    )
    success = await redis_manager.delete_initialized_server(current_user.id, server_key)
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
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
