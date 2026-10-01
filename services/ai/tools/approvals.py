"""Read-only approval cards for mutating AI tools."""

from __future__ import annotations

from typing import Any

from services.ai.tools.context import (
    ToolContext,
    tools,
)

from .approval_files import _execute_saved_host_command_summary, _patch_server_text_file_summary
from .approval_operations import (
    _change_current_map_summary,
    _control_server_summary,
    _execute_plugin_crash_isolation_summary,
    _restore_plugin_quarantine_summary,
    _run_server_operation_summary,
    _send_game_console_command_summary,
)
from .approval_plans import (
    _apply_github_plugin_install_summary,
    _apply_managed_plugin_upgrade_summary,
    _apply_plugin_plan_summary,
    _apply_server_startup_update_summary,
    _apply_workshop_map_summary,
)


async def build_approval_summary(
    name: str, arguments: dict[str, Any], context: ToolContext
) -> dict[str, Any]:
    """Build a fresh, read-only confirmation card for a mutating tool."""
    server = await tools._require_current_server(context)
    base: dict[str, Any] = {
        "server": {
            "id": server.id,
            "name": server.name,
            "host": server.host,
            "ssh_port": server.ssh_port,
            "game_port": server.game_port,
            "game_directory": server.game_directory,
        },
        "tool": name,
        "risk": "Changes remote server state",
    }
    handlers = {
        "patch_server_text_file": _patch_server_text_file_summary,
        "apply_plugin_plan": _apply_plugin_plan_summary,
        "apply_workshop_map": _apply_workshop_map_summary,
        "execute_plugin_crash_isolation": _execute_plugin_crash_isolation_summary,
        "restore_plugin_quarantine": _restore_plugin_quarantine_summary,
        "apply_github_plugin_install": _apply_github_plugin_install_summary,
        "run_server_operation": _run_server_operation_summary,
        "control_server": _control_server_summary,
        "send_game_console_command": _send_game_console_command_summary,
        "change_current_map": _change_current_map_summary,
        "apply_server_startup_update": _apply_server_startup_update_summary,
        "execute_saved_host_command": _execute_saved_host_command_summary,
        "apply_managed_plugin_upgrade": _apply_managed_plugin_upgrade_summary,
    }
    handler = handlers.get(name)
    if handler is not None:
        return await handler(arguments, context, server, base)
    return {**base, "arguments": arguments}
