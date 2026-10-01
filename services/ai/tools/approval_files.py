"""Read-only approval cards for mutating AI tools."""

from __future__ import annotations

import difflib
import hashlib
import posixpath
from typing import Any

from modules.models import Server
from services.ai.tools.context import (
    ToolContext,
    _safe_relative_path,
    tools,
)
from services.ai.tools.schemas import (
    FilePatchInput,
    SavedHostCommandInput,
)
from services.ai_security import redact_sensitive_text


async def _patch_server_text_file_summary(
    arguments: dict[str, Any], context: ToolContext, server: Server, base: dict[str, Any]
) -> dict[str, Any]:
    data = FilePatchInput.model_validate(arguments)
    relative = _safe_relative_path(data.relative_path)
    path = posixpath.join(server.game_directory.rstrip("/"), relative)
    manager = await tools._connect(server)
    try:
        valid, error = await manager.validate_path_within_base(
            server.game_directory,
            path,
            server,
            allow_missing=False,
            require_regular=True,
        )
        if not valid:
            raise ValueError(error)
        success, current, error = await manager.read_file(path, server, max_size=256_000)
        if not success:
            raise RuntimeError(error)
    finally:
        await manager.disconnect()
    if hashlib.sha256(current.encode()).hexdigest() != data.expected_revision:
        raise ValueError("File changed before approval; read it again")
    diff = "".join(
        difflib.unified_diff(
            redact_sensitive_text(current).splitlines(keepends=True),
            redact_sensitive_text(data.content).splitlines(keepends=True),
            fromfile=f"{relative} (current)",
            tofile=f"{relative} (proposed)",
        )
    )
    return {
        **base,
        "target": relative,
        "backup": "A timestamped backup will be created",
        "diff": redact_sensitive_text(diff, limit=12_000),
        "expected_result": "The file is replaced only if its SHA-256 revision still matches",
    }


async def _execute_saved_host_command_summary(
    arguments: dict[str, Any], context: ToolContext, server: Server, base: dict[str, Any]
) -> dict[str, Any]:
    data = SavedHostCommandInput.model_validate(arguments)
    command = await tools.CustomCommand.get_by_id_server_and_user(
        context.db, data.command_id, server.id, server.user_id
    )
    if command is None or command.target != "host":
        raise ValueError("Saved host command is unavailable")
    if tools._saved_command_hash(command) != data.expected_command_hash:
        raise ValueError("Saved host command changed before approval")
    return {
        **base,
        "command_id": command.id,
        "command_name": command.name,
        "command_hash": data.expected_command_hash,
        "full_command": redact_sensitive_text(command.commands, limit=12_000),
        "expected_result": "The exact saved host command revision is executed once",
    }
