"""Execute saved or one-time custom quick commands against a server."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any, Dict, List

from modules.models import Server
from services.game_session import (
    capture_console_command,
    find_running_session_manager,
    send_keys_command,
    session_name,
)

VALID_CUSTOM_COMMAND_TARGETS = frozenset({"game_process", "host"})
GAME_CONSOLE_CAPTURE_LINES = 200
GAME_CONSOLE_CAPTURE_POLL_SECONDS = 0.35
GAME_CONSOLE_CAPTURE_POLLS = 3
GAME_CONSOLE_OUTPUT_LIMIT = 12_000

_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_UNSAFE_TERMINAL_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1a\x1c-\x1f\x7f]")

if TYPE_CHECKING:
    from services.ssh_manager import SSHManager as SSHManagerType

SSHManager = None


def _ssh_manager():
    if SSHManager is not None:
        return SSHManager()
    from services.ssh_manager import SSHManager as Manager

    return Manager()


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
        console_output = (result.get("console_output") or "").strip()
        if console_output:
            lines.append("game console:")
            lines.append(console_output)
        console_capture_error = (result.get("console_capture_error") or "").strip()
        if console_capture_error:
            lines.append("game console capture error:")
            lines.append(console_capture_error)
        lines.append("")
    return chr(10).join(lines).strip()


def _clean_console_snapshot(output: str) -> str:
    cleaned = _ANSI_ESCAPE.sub("", output.replace("\r", ""))
    cleaned = _UNSAFE_TERMINAL_CONTROL.sub("", cleaned)
    return cleaned.rstrip()


def _new_console_output(before: str, after: str) -> str:
    """Extract lines appended to a bounded, potentially scrolling console snapshot."""
    before_lines = _clean_console_snapshot(before).splitlines()
    after_lines = _clean_console_snapshot(after).splitlines()
    if not after_lines or before_lines == after_lines:
        return ""
    if not before_lines:
        return "\n".join(after_lines)

    for overlap in range(min(len(before_lines), len(after_lines)), 0, -1):
        if before_lines[-overlap:] == after_lines[:overlap]:
            return "\n".join(after_lines[overlap:]).strip()

    # A full-screen redraw can replace the visible pane rather than append to
    # it. Returning the new snapshot is more useful than silently losing it.
    return "\n".join(after_lines).strip()


def _bounded_console_output(output: str) -> str:
    if len(output) <= GAME_CONSOLE_OUTPUT_LIMIT:
        return output
    return output[:GAME_CONSOLE_OUTPUT_LIMIT] + "\n[CONSOLE OUTPUT TRUNCATED]"


async def _capture_game_console_response(
    ssh_manager: SSHManagerType,
    manager: str,
    name: str,
    baseline: str | None,
) -> tuple[str, str, str | None]:
    """Poll briefly and return new console output plus its scope and any error."""
    capture_cmd = capture_console_command(manager, name, lines=GAME_CONSOLE_CAPTURE_LINES)
    latest = baseline or ""
    last_successful: str | None = None
    last_error: str | None = None

    for _ in range(GAME_CONSOLE_CAPTURE_POLLS):
        await asyncio.sleep(GAME_CONSOLE_CAPTURE_POLL_SECONDS)
        success, stdout, stderr = await ssh_manager.execute_command(capture_cmd, timeout=10)
        if not success:
            last_error = stderr or stdout or "Unable to capture the game console"
            continue

        cleaned = _clean_console_snapshot(stdout)
        if cleaned == last_successful and _new_console_output(baseline or "", cleaned):
            break
        latest = cleaned
        last_successful = cleaned
        last_error = None

    if last_successful is None:
        return "", "unavailable", last_error or "No console snapshot was returned"
    scope = "new_since_command" if baseline is not None else "recent_snapshot"
    output = _new_console_output(baseline or "", latest)
    return _bounded_console_output(output), scope, None


async def read_game_console(server: Server, *, lines: int = 120) -> Dict[str, Any]:
    """Read the current bounded console snapshot from a running game session."""
    if not 10 <= lines <= 500:
        raise CustomCommandError("Game console lines must be between 10 and 500")

    ssh_manager = _ssh_manager()
    connect_success, connect_message = await ssh_manager.connect(server)
    if not connect_success:
        raise RuntimeError(f"SSH connection failed: {connect_message}")

    try:
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
                "content": "",
            }

        command = capture_console_command(active_manager, name, lines=lines)
        success, stdout, stderr = await ssh_manager.execute_command(command, timeout=10)
        if not success:
            raise RuntimeError(stderr or stdout or "Unable to capture the game console")
        content = _bounded_console_output(_clean_console_snapshot(stdout))
        return {
            "success": True,
            "session_manager": active_manager,
            "lines_requested": lines,
            "content": content,
        }
    finally:
        await ssh_manager.disconnect()


async def execute_custom_commands(
    server: Server,
    target: str,
    commands: str,
    *,
    capture_game_output: bool = False,
) -> Dict[str, Any]:
    """Run command lines against the game process or host shell."""
    command_lines = parse_custom_command_lines(commands)
    if not command_lines:
        raise CustomCommandError("At least one command line is required")
    if target not in VALID_CUSTOM_COMMAND_TARGETS:
        raise CustomCommandError(f"Invalid custom command target: {target}")

    ssh_manager = _ssh_manager()
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
                baseline: str | None = None
                baseline_error: str | None = None
                if capture_game_output:
                    snapshot_cmd = capture_console_command(
                        active_manager,
                        name,
                        lines=GAME_CONSOLE_CAPTURE_LINES,
                    )
                    captured, snapshot, snapshot_error = await ssh_manager.execute_command(
                        snapshot_cmd,
                        timeout=10,
                    )
                    if captured:
                        baseline = _clean_console_snapshot(snapshot)
                    else:
                        baseline_error = (
                            snapshot_error or snapshot or "Unable to capture the initial console"
                        )

                input_cmd = send_keys_command(active_manager, name, command)
                success, stdout, stderr = await ssh_manager.execute_command(input_cmd, timeout=10)
                result = {
                    "index": index,
                    "command": command,
                    "success": success,
                    "stdout": stdout,
                    "stderr": stderr,
                }
                if capture_game_output and success:
                    (
                        console_output,
                        output_scope,
                        capture_error,
                    ) = await _capture_game_console_response(
                        ssh_manager,
                        active_manager,
                        name,
                        baseline,
                    )
                    result.update(
                        {
                            "console_output": console_output,
                            "console_output_scope": output_scope,
                        }
                    )
                    if capture_error:
                        result["console_capture_error"] = capture_error
                    elif baseline_error:
                        result["console_capture_note"] = (
                            "The initial snapshot was unavailable; console_output contains "
                            "the recent console snapshot instead of an exact delta."
                        )
                results.append(result)
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
