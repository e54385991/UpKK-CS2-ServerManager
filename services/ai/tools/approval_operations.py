"""Read-only approval cards for mutating AI tools."""

from __future__ import annotations

import hashlib
from typing import Any

from modules.models import Server
from services.ai.tools.context import (
    ToolContext,
)
from services.ai.tools.schemas import (
    ChangeCurrentMapInput,
    DiagnosticExecuteInput,
    DiagnosticRunInput,
    GameConsoleCommandInput,
)
from services.ai_security import redact_sensitive_text


async def _execute_plugin_crash_isolation_summary(
    arguments: dict[str, Any], context: ToolContext, server: Server, base: dict[str, Any]
) -> dict[str, Any]:
    data = DiagnosticExecuteInput.model_validate(arguments)
    from services.plugin_diagnostic_service import build_diagnostic_plan

    plan = await build_diagnostic_plan(context.db, context.user, server.id, data.scope)
    if plan["plan_hash"] != data.expected_plan_hash:
        raise ValueError("Diagnostic plan changed before approval")
    return {
        **base,
        "scope": data.scope,
        "candidate_groups": plan["candidates"],
        "maximum_starts": plan["health_policy"]["max_start_attempts"],
        "maximum_duration_seconds": plan["health_policy"]["max_duration_seconds"],
        "steps": [
            "stop before every isolation change",
            "verify baseline without third-party plugins",
            "narrow groups and confirm final candidates",
            "restore unrelated plugins and verify final health",
        ],
        "expected_result": "Only reproduced crash candidates remain quarantined",
    }


async def _restore_plugin_quarantine_summary(
    arguments: dict[str, Any], context: ToolContext, server: Server, base: dict[str, Any]
) -> dict[str, Any]:
    data = DiagnosticRunInput.model_validate(arguments)
    from services.plugin_diagnostic_service import get_diagnostic_run

    diagnostic = await get_diagnostic_run(context.db, context.user, server.id, data.diagnostic_id)
    return {
        **base,
        "diagnostic_id": data.diagnostic_id,
        "quarantine": diagnostic["quarantine"],
        "expected_result": "All items in the immutable diagnostic manifest are restored",
    }


async def _run_server_operation_summary(
    arguments: dict[str, Any], context: ToolContext, server: Server, base: dict[str, Any]
) -> dict[str, Any]:
    operation = arguments["operation"]
    if operation in {"install_metamod", "install_counterstrikesharp"}:
        return {
            **base,
            "operation": operation,
            "installation_method": "panel_native",
            "steps": [
                "install with the panel framework installer",
                "record framework tracking metadata",
                "require a separate restart before generated config inspection",
            ],
            "expected_result": (
                "The framework is installed through the panel and reports restart_required"
            ),
        }
    return {
        **base,
        "operation": operation,
        "expected_result": "CS2 deployment files may be downloaded or validated",
    }


async def _control_server_summary(
    arguments: dict[str, Any], context: ToolContext, server: Server, base: dict[str, Any]
) -> dict[str, Any]:
    return {
        **base,
        "operation": arguments["action"],
        "expected_result": "The selected server process state changes",
    }


async def _send_game_console_command_summary(
    arguments: dict[str, Any], context: ToolContext, server: Server, base: dict[str, Any]
) -> dict[str, Any]:
    data = GameConsoleCommandInput.model_validate(arguments)
    return {
        **base,
        "target": "Running CS2 game-process console (not host Shell)",
        "command": redact_sensitive_text(data.command, limit=500),
        "command_hash": hashlib.sha256(data.command.encode()).hexdigest(),
        "steps": [
            "acquire the server maintenance lock",
            "locate the exact configured or legacy screen/tmux session",
            "snapshot the current bounded game-console output",
            "send the approved command as literal input followed by Enter",
            "poll briefly and return newly observed console output to the agent",
        ],
        "expected_result": (
            "The command is delivered once to the running game process and newly observed "
            "console output is returned; it is not interpreted as host Shell output"
        ),
    }


async def _change_current_map_summary(
    arguments: dict[str, Any], context: ToolContext, server: Server, base: dict[str, Any]
) -> dict[str, Any]:
    data = ChangeCurrentMapInput.model_validate(arguments)
    from services.change_map_service import load_map_pool, resolve_unique_map

    candidate = resolve_unique_map(await load_map_pool(server), data.query)
    command = candidate.command
    return {
        **base,
        "target": "Running CS2 game-process console (not host Shell)",
        "map": candidate.to_public_dict(),
        "command": redact_sensitive_text(command, limit=500),
        "command_hash": hashlib.sha256(command.encode()).hexdigest(),
        "steps": [
            "resolve one map from the MapChooser pool by name or Workshop ID",
            "acquire the server maintenance lock",
            "send host_workshop_map {id} for Workshop maps, or map {name} for official maps",
        ],
        "expected_result": "The running CS2 process changes to the resolved map",
    }
