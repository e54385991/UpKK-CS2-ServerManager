"""Discord confirmation-plan builders."""

from __future__ import annotations

from modules.models import DiscordOperationRun, Server
from services.change_map_service import MapCandidate
from services.compat import LateBoundModule

host = LateBoundModule("services.discord_bot_manager")


def _simple_plan(server: Server, action: str) -> dict:
    return {
        "server_id": server.id,
        "server_name": server.name,
        "action": action,
        "steps": ["acquire maintenance lock", f"execute {action}", "report final result"],
    }


def _game_console_plan(server: Server, command: str) -> dict:
    """Build a deterministic public plan without persisting the raw command in JSON."""

    return {
        "server_id": server.id,
        "server_name": server.name,
        "action": "game_console",
        "target": "running CS2 game-process console (not host Shell)",
        "command": host.redact_sensitive_text(command, limit=500),
        "command_hash": host.hashlib.sha256(command.encode()).hexdigest(),
        "steps": [
            "acquire maintenance lock",
            "locate the exact screen/tmux game session",
            "send literal command input followed by Enter",
        ],
    }


def _operation_game_console_command(item: DiscordOperationRun) -> str:
    encrypted = str(item.arguments.get("command_encrypted") or "")
    command = host.decrypt_credential(encrypted)
    if not command:
        raise host.DiscordOperationDenied("Game console command is unavailable")
    expected_hash = str(item.arguments.get("command_hash") or "")
    if host.hashlib.sha256(command.encode()).hexdigest() != expected_hash:
        raise host.DiscordOperationDenied("Game console command changed after planning")
    return host.GameConsoleCommandInput(command=command).command


def _change_map_plan(server: Server, candidate: MapCandidate) -> dict:
    command = candidate.command
    return {
        "server_id": server.id,
        "server_name": server.name,
        "action": "change_map",
        "map_name": candidate.name,
        "workshop_id": candidate.workshop_id or None,
        "command": host.redact_sensitive_text(command, limit=500),
        "command_hash": host.hashlib.sha256(command.encode()).hexdigest(),
        "steps": [
            "acquire maintenance lock",
            "locate the exact screen/tmux game session",
            "send the resolved change-map command followed by Enter",
        ],
    }


def _operation_change_map_candidate(item: DiscordOperationRun) -> MapCandidate:
    command = _operation_game_console_command(item)
    candidate = MapCandidate(
        name=str(item.arguments.get("name") or ""),
        workshop_id=str(item.arguments.get("workshop_id") or ""),
        filename=str(item.arguments.get("filename") or ""),
    )
    if candidate.command != command:
        raise host.DiscordOperationDenied("Change-map command changed after planning")
    return candidate
