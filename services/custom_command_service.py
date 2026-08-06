"""Execute saved or one-time custom quick commands against a server."""

from __future__ import annotations

from typing import Any, Dict, List

from modules.models import Server
from services.game_session import (
    find_running_session_manager,
    send_keys_command,
    session_name,
)
from services.ssh_manager import SSHManager

VALID_CUSTOM_COMMAND_TARGETS = frozenset({"game_process", "host"})


class CustomCommandError(ValueError):
    """Raised when custom command input is invalid."""


def parse_custom_command_lines(commands: str) -> List[str]:
    return [line.strip() for line in commands.splitlines() if line.strip()]


def format_custom_command_log(target: str, command_results: List[Dict[str, Any]]) -> str:
    lines = [f"Target: {target}", ""]
    for result in command_results:
        status_text = "OK" if result.get("success") else "FAIL"
        lines.append(f"[{status_text}] #{result.get('index')}: {result.get('command')}")
        stdout = (result.get("stdout") or "").strip()
        stderr = (result.get("stderr") or "").strip()
        if stdout:
            lines.append("stdout:")
            lines.append(stdout)
        if stderr:
            lines.append("stderr:")
            lines.append(stderr)
        lines.append("")
    return chr(10).join(lines).strip()


async def execute_custom_commands(
    server: Server,
    target: str,
    commands: str,
) -> Dict[str, Any]:
    """Run command lines against the game process or host shell."""
    command_lines = parse_custom_command_lines(commands)
    if not command_lines:
        raise CustomCommandError("At least one command line is required")
    if target not in VALID_CUSTOM_COMMAND_TARGETS:
        raise CustomCommandError(f"Invalid custom command target: {target}")

    ssh_manager = SSHManager()
    connect_success, connect_message = await ssh_manager.connect(server)
    if not connect_success:
        return {
            "success": False,
            "message": f"SSH connection failed: {connect_message}",
            "target": target,
            "results": [],
        }

    results: List[Dict[str, Any]] = []
    try:
        if target == "game_process":
            name = session_name(server.id)
            active_manager = await find_running_session_manager(
                ssh_manager.execute_command,
                server.session_manager,
                name,
            )
            if not active_manager:
                return {
                    "success": False,
                    "message": "Game server is not running. Please start the server first.",
                    "target": target,
                    "results": [],
                }

            for index, command in enumerate(command_lines, start=1):
                input_cmd = send_keys_command(active_manager, name, command)
                success, stdout, stderr = await ssh_manager.execute_command(input_cmd, timeout=10)
                results.append(
                    {
                        "index": index,
                        "command": command,
                        "success": success,
                        "stdout": stdout,
                        "stderr": stderr,
                    }
                )
        else:
            for index, command in enumerate(command_lines, start=1):
                success, stdout, stderr = await ssh_manager.execute_command(command, timeout=300)
                results.append(
                    {
                        "index": index,
                        "command": command,
                        "success": success,
                        "stdout": stdout,
                        "stderr": stderr,
                    }
                )
    finally:
        await ssh_manager.disconnect()

    failed_count = len([result for result in results if not result["success"]])
    total_count = len(results)
    success = failed_count == 0
    message = (
        f"Executed {total_count} command(s) successfully"
        if success
        else f"Executed {total_count} command(s), {failed_count} failed"
    )
    return {
        "success": success,
        "message": message,
        "target": target,
        "results": results,
    }
