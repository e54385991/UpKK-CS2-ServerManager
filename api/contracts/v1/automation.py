"""Restricted automation input and discoverable game-command contracts."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StrictStr

from api.contracts.base import ApiRequest, ApiResponse


class AutomationRestartRequest(ApiRequest):
    action: Literal["restart"]


class AutomationCommandRequest(ApiRequest):
    action: Literal["send_game_command"]
    command: StrictStr = Field(min_length=1, max_length=64)
    args: list[StrictStr] = Field(max_length=16)


AutomationRequest = Annotated[
    AutomationRestartRequest | AutomationCommandRequest, Field(discriminator="action")
]


class GameCommandArgumentView(ApiResponse):
    name: str
    description: str
    pattern: str
    max_length: int


class GameCommandView(ApiResponse):
    name: str
    description: str
    arguments: list[GameCommandArgumentView]
    requires_empty: bool
    requires_online: bool
    max_human_players: int


class AutomationStatusView(ApiResponse):
    server_id: int
    status: str
    query_ok: bool
    player_count: int | None
    bot_count: int | None
    human_count: int | None
    max_players: int | None
    version: str | None
    map_name: str | None
    checked_at: datetime
    restart_allowed: bool
    restart_reason: str
