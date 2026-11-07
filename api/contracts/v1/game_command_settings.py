"""Personal game-command editor DTOs, separate from runtime discovery contracts."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from api.contracts.base import ApiRequest, ApiResponse
from services.automation.registry import CommandSetting


class GameArgumentSettingRequest(ApiRequest):
    name: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    description: StrictStr = Field(min_length=1, max_length=300)
    kind: Literal["token", "integer", "choice"] = "token"
    max_length: StrictInt = Field(default=128, ge=1, le=128)
    minimum: StrictInt = Field(default=1, ge=0, le=999999999)
    maximum: StrictInt = Field(default=86400, ge=0, le=999999999)
    choices: list[
        Annotated[StrictStr, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_]+$")]
    ] = Field(default_factory=list, max_length=32)


class GameCommandSettingRequest(ApiRequest):
    name: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    description: StrictStr = Field(min_length=1, max_length=500)
    arguments: list[GameArgumentSettingRequest] = Field(default_factory=list, max_length=16)
    enabled: StrictBool = True
    requires_empty: StrictBool = True
    requires_online: StrictBool = True

    @model_validator(mode="after")
    def validate_definition(self) -> GameCommandSettingRequest:
        CommandSetting.model_validate(self.model_dump())
        return self


class GameCommandSettingsRequest(ApiRequest):
    commands: list[GameCommandSettingRequest] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_names(self) -> GameCommandSettingsRequest:
        if len({item.name for item in self.commands}) != len(self.commands):
            raise ValueError("Command names must be unique.")
        return self


class GameArgumentSettingView(ApiResponse):
    name: str
    description: str
    kind: Literal["token", "integer", "choice"]
    max_length: int
    minimum: int
    maximum: int
    choices: list[str]


class GameCommandSettingView(ApiResponse):
    name: str
    description: str
    arguments: list[GameArgumentSettingView]
    enabled: bool
    requires_empty: bool
    requires_online: bool


class GameCommandSettingsView(ApiResponse):
    commands: list[GameCommandSettingView]
