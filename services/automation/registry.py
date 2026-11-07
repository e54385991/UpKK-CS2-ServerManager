"""Account-owned command definitions with bounded, generated argument patterns."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from modules import User

from .commands import GAME_COMMANDS, AutomationDenied, CommandArgument, GameCommand


class ArgumentSetting(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=300)
    kind: Literal["token", "integer", "choice"] = "token"
    max_length: int = Field(default=128, ge=1, le=128)
    minimum: int = Field(default=1, ge=0, le=999999999)
    maximum: int = Field(default=86400, ge=0, le=999999999)
    choices: list[
        Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_]+$")]
    ] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_rule(self) -> ArgumentSetting:
        if self.minimum > self.maximum:
            raise ValueError("The minimum must not exceed the maximum.")
        if self.kind == "integer" and len(str(self.maximum)) > self.max_length:
            raise ValueError("The maximum integer exceeds the argument length limit.")
        if self.kind == "choice" and (
            not self.choices
            or len(set(self.choices)) != len(self.choices)
            or any(len(item) > self.max_length for item in self.choices)
        ):
            raise ValueError("Choices must be unique ASCII tokens within the length limit.")
        if len(self.contract().pattern) > 512:
            raise ValueError(
                "The generated argument rule is too large. Use fewer choices or a simpler range."
            )
        return self

    def contract(self) -> CommandArgument:
        if self.kind == "integer":
            pattern = integer_pattern(self.minimum, self.maximum)
        elif self.kind == "choice":
            pattern = "(?:" + "|".join(re.escape(value) for value in self.choices) + ")"
        else:
            pattern = rf"[A-Za-z0-9_]{{1,{self.max_length}}}"
        return CommandArgument(self.name, self.description, pattern, self.max_length)


class CommandSetting(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=500)
    arguments: list[ArgumentSetting] = Field(default_factory=list, max_length=16)
    enabled: bool = True
    requires_empty: bool = True
    requires_online: bool = True

    @model_validator(mode="after")
    def validate_policy(self) -> CommandSetting:
        if not self.requires_online:
            raise ValueError("Registered game commands require an online server.")
        names = [rule.name for rule in self.arguments]
        if len(names) != len(set(names)):
            raise ValueError("Argument names must be unique within a command.")
        if self.name == "css_wsmap" and not self.requires_empty:
            raise ValueError("Map changes must require zero human players.")
        if (
            self.name in {"css_wsmap", "css_ext_maptime"}
            and self.arguments != default_setting(self.name).arguments
        ):
            raise ValueError("Built-in map and extension argument rules cannot be weakened.")
        return self

    def contract(self) -> GameCommand:
        return GameCommand(
            self.name,
            self.description,
            tuple(rule.contract() for rule in self.arguments),
            self.requires_empty,
            self.requires_online,
        )


def _digit_range(low: str, high: str) -> str:
    if not low:
        return ""
    if low == high:
        return low
    if low == "0" * len(low) and high == "9" * len(low):
        return rf"[0-9]{{{len(low)}}}"
    if low[0] == high[0]:
        return low[0] + _digit_range(low[1:], high[1:])
    tail = len(low) - 1
    parts = [low[0] + _digit_range(low[1:], "9" * tail)]
    first, last = int(low[0]) + 1, int(high[0]) - 1
    if first <= last:
        digit = str(first) if first == last else f"[{first}-{last}]"
        parts.append(digit + (rf"[0-9]{{{tail}}}" if tail else ""))
    parts.append(high[0] + _digit_range("0" * tail, high[1:]))
    return "(?:" + "|".join(parts) + ")"


def integer_pattern(minimum: int, maximum: int) -> str:
    parts = []
    for width in range(len(str(minimum)), len(str(maximum)) + 1):
        low = max(minimum, 0 if width == 1 else 10 ** (width - 1))
        high = min(maximum, 10**width - 1)
        parts.append(_digit_range(str(low), str(high)))
    return "(?:" + "|".join(parts) + ")"


def default_setting(name: str) -> CommandSetting:
    definition = GAME_COMMANDS[name]
    argument = definition.arguments[0]
    # Construct trusted defaults without recursively validating their own invariants.
    return CommandSetting.model_construct(
        name=name,
        description=definition.description,
        enabled=True,
        requires_empty=definition.requires_empty,
        requires_online=True,
        arguments=[
            ArgumentSetting(
                name=argument.name,
                description=argument.description,
                kind="integer" if name == "css_ext_maptime" else "token",
                max_length=argument.max_length,
            )
        ],
    )


def settings_for(user: User) -> list[CommandSetting]:
    saved = getattr(user, "automation_commands", None)
    if saved is None:
        return [default_setting(name) for name in ("css_ext_maptime", "css_wsmap")]
    try:
        if not isinstance(saved, list) or len(saved) > 100:
            raise ValueError
        result = [CommandSetting.model_validate(item) for item in saved]
        if len({item.name for item in result}) != len(result):
            raise ValueError
        return result
    except (ValidationError, ValueError, TypeError) as exc:
        raise AutomationDenied(
            "The personal game-command registry is invalid. Review it in personal settings."
        ) from exc


def commands_for(user: User) -> dict[str, GameCommand]:
    if getattr(user, "automation_commands", None) is None:
        return dict(GAME_COMMANDS)
    return {item.name: item.contract() for item in settings_for(user) if item.enabled}


def resolve_command(user: User, name: str) -> GameCommand:
    command = commands_for(user).get(name)
    if command is None:
        raise AutomationDenied(
            "This game command is not registered or enabled in your personal settings."
        )
    return command


async def save_settings(db: AsyncSession, user: User, definitions: list[CommandSetting]) -> None:
    """Replace one account's validated registry without modifying global registrations."""
    user.automation_commands = [item.model_dump() for item in definitions]
    await db.commit()
