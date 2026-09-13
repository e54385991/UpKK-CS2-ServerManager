"""Shared AI tool context, specs, and path helpers."""

from __future__ import annotations

import hashlib
import json
import posixpath
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from modules.models import CustomCommand, Server, User
from modules.schemas.discord import AgentCapability
from services.ai.tools.schemas import ToolInput
from services.ssh_manager import SSHManager

EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


class _ToolsHost:
    """Resolve patchable names through ``services.ai_tools`` at call time."""

    def __getattr__(self, name: str) -> Any:
        from services import ai_tools

        return getattr(ai_tools, name)


tools: Any = _ToolsHost()


@dataclass(slots=True)
class ToolContext:
    db: AsyncSession
    user: User
    server: Server | None
    emit: EventEmitter
    run_id: str | None = None
    enforce_agent_policy: bool = True


# The registry intentionally stores handlers for different Pydantic input
# models.  Validation happens immediately before dispatch; ``Any`` is limited
# to this heterogeneous adapter boundary rather than business logic.
ToolHandler = Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]
CapabilityResolver = Callable[[dict[str, Any]], frozenset[AgentCapability]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    risk: Literal["read", "write", "destructive"]
    input_model: type[ToolInput]
    handler: ToolHandler
    requires_server: bool = True
    capability_options: tuple[frozenset[AgentCapability], ...] = ()
    capability_resolver: CapabilityResolver | None = None

    def required_capabilities(self, arguments: dict[str, Any]) -> frozenset[AgentCapability]:
        if self.capability_resolver is not None:
            return self.capability_resolver(arguments)
        if len(self.capability_options) == 1:
            return self.capability_options[0]
        return frozenset()

    def is_exposed(self, allowed: frozenset[AgentCapability]) -> bool:
        return not self.capability_options or any(
            option <= allowed for option in self.capability_options
        )

    def api_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


async def _require_current_server(ctx: ToolContext) -> Server:
    if ctx.server is None or ctx.server.id is None:
        raise ValueError("Select a server before using this tool")
    return await tools.authorized_server(ctx.db, ctx.user, ctx.server.id)


async def _require_active_user(ctx: ToolContext) -> User:
    user = await ctx.db.get(User, ctx.user.id)
    if user is None or not user.is_active:
        raise PermissionError("The current user is no longer active")
    return user


def _saved_command_hash(command: CustomCommand) -> str:
    payload = {
        "id": command.id,
        "target": command.target,
        "commands": command.commands,
        "updated_at": command.updated_at.isoformat() if command.updated_at else None,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _safe_relative_path(relative_path: str) -> str:
    value = relative_path.replace("\\", "/").strip()
    normalized = posixpath.normpath(value)
    if (
        not value
        or value.startswith("/")
        or normalized == ".."
        or normalized.startswith("../")
        or "\x00" in value
    ):
        raise ValueError("Path must remain inside the managed game directory")
    return normalized


async def _connect(server: Server) -> SSHManager:
    manager = tools.SSHManager()
    success, message = await manager.connect(server)
    if not success:
        raise RuntimeError(f"SSH connection failed: {message}")
    return manager


def canonical_arguments(arguments: dict[str, Any]) -> tuple[str, str]:
    serialized = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return serialized, hashlib.sha256(serialized.encode()).hexdigest()
