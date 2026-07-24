"""Shared definitions for the modules/schemas domain modules."""

# ruff: noqa: F401

import re
from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import EmailStr, field_validator, model_validator
from sqlmodel import Field, SQLModel

from modules.models import ServerStatus

ALLOWED_SERVER_ACTIONS = [
    "deploy",
    "start",
    "stop",
    "restart",
    "status",
    "update",
    "validate",
    "install_metamod",
    "install_counterstrikesharp",
    "install_cs2fixes",
    "install_swiftly",
    "update_metamod",
    "update_counterstrikesharp",
    "update_cs2fixes",
    "update_swiftly",
    "backup_plugins",
]

SERVER_ACTION_PATTERN = f"^({'|'.join(ALLOWED_SERVER_ACTIONS)})$"

ALLOWED_SCHEDULED_TASK_ACTIONS = [
    "start",
    "stop",
    "restart",
    "update",
    "validate",
    "backup_plugins",
]

SCHEDULED_TASK_ACTION_PATTERN = f"^({'|'.join(ALLOWED_SCHEDULED_TASK_ACTIONS)})$"

ALLOWED_BATCH_ACTIONS = ["restart", "stop", "update"]

BATCH_ACTION_PATTERN = f"^({'|'.join(ALLOWED_BATCH_ACTIONS)})$"

ALLOWED_PLUGINS = ["metamod", "counterstrikesharp", "cs2fixes"]

MAX_BATCH_SERVERS = 40


def _unique_server_ids(server_ids: List[int]) -> List[int]:
    """Prevent duplicate work while preserving the caller's order."""
    return list(dict.fromkeys(server_ids))


CUSTOM_COMMAND_TARGETS = ["game_process", "host"]


def _validate_custom_command_text(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("Commands cannot be empty")
    if "\x00" in v:
        raise ValueError("Commands contain invalid null characters")
    command_lines = [line.strip() for line in v.splitlines() if line.strip()]
    if not command_lines:
        raise ValueError("At least one command line is required")
    if len(command_lines) > 100:
        raise ValueError("At most 100 command lines are allowed")
    for line in command_lines:
        if len(line) > 2000:
            raise ValueError("Each command line must be at most 2000 characters")
    return "\n".join(command_lines)


__all__ = [name for name in globals() if not name.startswith("__")]
