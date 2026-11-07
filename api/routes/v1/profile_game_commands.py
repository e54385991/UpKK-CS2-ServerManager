"""JWT-only personal registry editing; API keys retain discovery/submission only."""

from fastapi import APIRouter, HTTPException, Request

from api.contracts.v1.game_command_settings import (
    GameCommandSettingsRequest,
    GameCommandSettingsView,
)
from api.dependencies import ActiveUser, DatabaseSession
from services.audit_log_service import record_audit_event
from services.automation.commands import AutomationDenied
from services.automation.registry import CommandSetting, save_settings, settings_for

router = APIRouter(prefix="/api/v1/profile/game-commands", tags=["v1-profile"])


def _view(user) -> GameCommandSettingsView:
    try:
        return GameCommandSettingsView.model_validate(
            {"commands": [item.model_dump() for item in settings_for(user)]}
        )
    except AutomationDenied as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=GameCommandSettingsView)
async def read_game_command_settings(current_user: ActiveUser) -> GameCommandSettingsView:
    return _view(current_user)


@router.put("", response_model=GameCommandSettingsView)
async def save_game_command_settings(
    body: GameCommandSettingsRequest,
    current_user: ActiveUser,
    db: DatabaseSession,
    request: Request,
) -> GameCommandSettingsView:
    await save_settings(
        db,
        current_user,
        [CommandSetting.model_validate(item.model_dump()) for item in body.commands],
    )
    await record_audit_event(
        category="settings",
        action="personal_game_commands_updated",
        status="success",
        user=current_user,
        request=request,
        details={"command_count": len(body.commands)},
    )
    return _view(current_user)
