"""Versioned saved and one-time quick commands for the Next console."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from fastapi import status as http_status

from api.dependencies import ActiveUser, DatabaseSession
from api.routes.servers import configuration as legacy
from modules import CustomCommandCreate, CustomCommandExecuteRequest, CustomCommandUpdate
from services.custom_command_service import format_custom_command_log

from . import schemas as v1_schemas
from .schemas import (
    ActionResult,
    CustomCommandExecuteView,
    CustomCommandView,
    CustomCommandWriteRequest,
)

router = APIRouter(prefix="/api/v1/servers", tags=["v1-custom-commands"])

CommandTarget = Literal["host", "game_process"]


def _target(value: object) -> CommandTarget:
    return "game_process" if str(value) == "game_process" else "host"


def _view(command) -> CustomCommandView:
    return CustomCommandView(
        id=int(command.id),
        server_id=int(command.server_id),
        name=str(command.name),
        target=_target(command.target),
        commands=str(command.commands),
        created_at=getattr(command, "created_at", None),
        updated_at=getattr(command, "updated_at", None),
    )


def _execute_view(response) -> CustomCommandExecuteView:
    payload = getattr(response, "data", None) or {}
    results = payload.get("results") if isinstance(payload, dict) else None
    target = ""
    if isinstance(payload, dict):
        target = str(payload.get("target") or "")
    log = ""
    if isinstance(results, list) and results:
        log = format_custom_command_log(target or "host", results)
    return CustomCommandExecuteView(
        success=bool(getattr(response, "success", False)),
        message=str(getattr(response, "message", "")),
        log=log,
    )


@router.get("/{server_id}/custom-commands", response_model=list[CustomCommandView])
async def list_custom_commands(
    server_id: int, db: DatabaseSession, current_user: ActiveUser
) -> list[CustomCommandView]:
    commands = await legacy.list_custom_commands(server_id, db, current_user)
    return [_view(command) for command in commands]


@router.post(
    "/{server_id}/custom-commands",
    response_model=CustomCommandView,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_custom_command(
    server_id: int,
    body: CustomCommandWriteRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> CustomCommandView:
    command = await legacy.create_custom_command(
        server_id,
        CustomCommandCreate(name=body.name, target=body.target, commands=body.commands),
        db,
        current_user,
    )
    return _view(command)


@router.put("/{server_id}/custom-commands/{command_id}", response_model=CustomCommandView)
async def update_custom_command(
    server_id: int,
    command_id: int,
    body: CustomCommandWriteRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> CustomCommandView:
    command = await legacy.update_custom_command(
        server_id,
        command_id,
        CustomCommandUpdate(name=body.name, target=body.target, commands=body.commands),
        db,
        current_user,
    )
    return _view(command)


@router.delete("/{server_id}/custom-commands/{command_id}", response_model=ActionResult)
async def delete_custom_command(
    server_id: int,
    command_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ActionResult:
    result = await legacy.delete_custom_command(server_id, command_id, db, current_user)
    return ActionResult(
        success=bool(getattr(result, "success", True)),
        message=str(getattr(result, "message", "Custom command deleted successfully")),
    )


@router.post(
    "/{server_id}/custom-commands/execute",
    response_model=CustomCommandExecuteView,
)
async def execute_one_time_custom_command(
    server_id: int,
    body: v1_schemas.CustomCommandExecuteBody,
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
) -> CustomCommandExecuteView:
    result = await legacy.execute_one_time_custom_command(
        server_id,
        CustomCommandExecuteRequest(target=body.target, commands=body.commands),
        db,
        current_user,
        request,
    )
    return _execute_view(result)


@router.post(
    "/{server_id}/custom-commands/{command_id}/execute",
    response_model=CustomCommandExecuteView,
)
async def execute_saved_custom_command(
    server_id: int,
    command_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
) -> CustomCommandExecuteView:
    result = await legacy.execute_saved_custom_command(
        server_id, command_id, db, current_user, request
    )
    return _execute_view(result)
