"""Registered single-line game commands and their public parameter contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass


class AutomationDenied(ValueError):
    """An automation request cannot be executed safely."""


@dataclass(frozen=True)
class CommandArgument:
    name: str
    description: str
    pattern: str
    max_length: int = 128

    def validate(self, value: str) -> str:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= self.max_length
            or any(char in value for char in ";\r\n\x00")
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or re.fullmatch(self.pattern, value, flags=re.ASCII) is None
        ):
            raise AutomationDenied(f"Invalid {self.name}. {self.description}")
        return value


@dataclass(frozen=True)
class GameCommand:
    name: str
    description: str
    arguments: tuple[CommandArgument, ...]
    requires_empty: bool = True
    requires_online: bool = True

    def render(self, args: list[str]) -> str:
        if len(args) != len(self.arguments):
            raise AutomationDenied(f"{self.name} requires {len(self.arguments)} argument(s).")
        validated = [rule.validate(value) for rule, value in zip(self.arguments, args, strict=True)]
        return " ".join((self.name, *validated))


GAME_COMMANDS: dict[str, GameCommand] = {
    "css_ext_maptime": GameCommand(
        name="css_ext_maptime",
        description="Send a map-time extension in seconds. The resulting time limit is not verified.",
        arguments=(
            CommandArgument(
                name="seconds",
                description="Use an integer from 1 to 86400 seconds.",
                pattern=r"(?:[1-9][0-9]{0,3}|[1-7][0-9]{4}|8[0-5][0-9]{3}|86[0-3][0-9]{2}|86400)",
                max_length=5,
            ),
        ),
        requires_empty=False,
    ),
    "css_wsmap": GameCommand(
        name="css_wsmap",
        description="Send a Workshop map-change command. Map transition is not verified.",
        arguments=(
            CommandArgument(
                name="map_name",
                description="Use 1-128 ASCII letters, digits, or underscores.",
                pattern=r"[A-Za-z0-9_]{1,128}",
            ),
        ),
    ),
}


def game_command(name: str) -> GameCommand:
    """Resolve an explicitly registered command, never an arbitrary console line."""
    command = GAME_COMMANDS.get(name)
    if command is None or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", command.name) is None:
        raise AutomationDenied("This game command is not registered for automation.")
    return command
