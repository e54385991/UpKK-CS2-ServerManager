"""Personal-key automation with a fixed lifecycle and registered console surface."""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, status

from api.contracts.v1.automation import (
    AutomationCommandRequest,
    AutomationRequest,
    AutomationStatusView,
    GameCommandView,
)
from api.dependencies import (
    AutomationUser,
    DatabaseSession,
    close_request_session,
    require_server_access,
)
from services.automation.commands import GAME_COMMANDS, AutomationDenied, game_command
from services.automation.status import read_automation_status
from services.server_compatibility import (
    execstack_cleanup_enabled_for_action,
    execstack_targets_metadata,
)
from services.server_operation_hub import ServerOperationConflict

from .operation_runner.server import enqueue_game_console_command, enqueue_server_operation
from .operations import to_view
from .schemas import ServerOperationView

router = APIRouter(tags=["v1-automation"])


@router.get("/api/v1/automation/game-commands", response_model=list[GameCommandView])
async def list_game_commands(current_user: AutomationUser) -> list[GameCommandView]:
    """Discover definitions, not proof that a game plugin is installed or running."""
    return [GameCommandView.model_validate(asdict(command)) for command in GAME_COMMANDS.values()]


@router.get("/api/v1/servers/{server_id}/automation-status", response_model=AutomationStatusView)
async def get_automation_status(
    server_id: int, db: DatabaseSession, current_user: AutomationUser
) -> AutomationStatusView:
    """Read live counts/version; eligibility is advisory, never authorization to execute."""
    server = await require_server_access(db, server_id, current_user)
    await close_request_session(db)
    return AutomationStatusView.model_validate(asdict(await read_automation_status(server)))


@router.post(
    "/api/v1/servers/{server_id}/automation-operations",
    response_model=ServerOperationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_automation_operation(
    server_id: int,
    body: AutomationRequest,
    db: DatabaseSession,
    current_user: AutomationUser,
) -> ServerOperationView:
    server = await require_server_access(db, server_id, current_user)
    try:
        if isinstance(body, AutomationCommandRequest):
            definition = game_command(body.command)
            command = definition.render(body.args)
            record = await enqueue_game_console_command(
                server_id=server_id,
                command=command,
                actor_user_id=current_user.id,
                automation_command={"name": body.command, "args": list(body.args)},
            )
        else:
            extra: dict[str, object] = {
                "clear_execstack": execstack_cleanup_enabled_for_action(server, "restart")
            }
            if extra["clear_execstack"]:
                extra.update(execstack_targets_metadata(server))
            extra["automation_guard"] = "empty_or_stopped"
            record = await enqueue_server_operation(
                server_id=server_id,
                action="restart",
                actor_user_id=current_user.id,
                extra=extra,
            )
    except AutomationDenied as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ServerOperationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return to_view(record)
