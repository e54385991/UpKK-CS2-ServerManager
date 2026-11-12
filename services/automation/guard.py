"""Live occupancy checks and conservative proof of an unexpectedly stopped game."""

from __future__ import annotations

import re

from modules import Server
from services.a2s_cache_service import a2s_cache_service
from services.ssh_manager import SSHManager
from services.telemetry_runtime import ssh_probe_limiter

from .commands import AutomationDenied


def _related_processes(output: str, server: Server) -> bool:
    """Treat ambiguous CS2 processes as live; never mask a failed process scan."""
    if len(output) > 2_000_000:
        raise AutomationDenied("The remote process scan exceeded its safety limit.")
    for line in output.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3 or not fields[0].isdigit():
            raise AutomationDenied("The remote process scan could not be verified.")
        _, executable, arguments = fields
        if executable == "cs2":
            ports = re.findall(r"(?:^|\s)-port(?:\s+|=)(\d+)(?=\s|$)", arguments)
            if not ports or str(server.game_port) in ports:
                return True
        if "cs2_autorestart.sh" in arguments and server.game_directory in arguments:
            return True
    return False


def _listening(output: str, port: int) -> bool:
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 5:
            raise AutomationDenied("The remote listening-port scan could not be verified.")
        if re.search(rf":{port}$", fields[4]):
            return True
    return False


async def _confirm_stopped(server: Server, manager: SSHManager) -> None:
    key = (server.host.strip().lower(), int(server.ssh_port))
    async with ssh_probe_limiter.slot(key):
        try:
            ok, state = await manager.get_server_status(server)
            if not ok or state != "stopped":
                raise AutomationDenied(
                    "No live player count or verified stopped game is available."
                )
            connected, _ = await manager.connect(server)
            if not connected:
                raise AutomationDenied("SSH could not verify the stopped game.")
            ok, processes, _ = await manager.execute_command("ps -eo pid=,comm=,args=", timeout=10)
            if not ok or not processes.strip() or _related_processes(processes, server):
                raise AutomationDenied("The game process is running or its absence is unverified.")
            ok, listeners, _ = await manager.execute_command("ss -H -lunt", timeout=10)
            if not ok or _listening(listeners, server.game_port):
                raise AutomationDenied("The game port is listening or its absence is unverified.")
            # A second session observation rejects a wrapper that started during the scan.
            ok, state = await manager.get_server_status(server)
            if not ok or state != "stopped":
                raise AutomationDenied("The game state changed during offline verification.")
        finally:
            await manager.disconnect()


async def ensure_eligible(
    server: Server,
    manager: SSHManager,
    *,
    allow_stopped: bool = False,
    requires_empty: bool = True,
    requires_online: bool = True,
    max_human_players: int | None = None,
) -> None:
    """Use this probe's response, never the cached snapshot left by another probe."""
    if server.manual_stop_requested:
        raise AutomationDenied("An administrator requested that this server remain stopped.")
    if max_human_players is None:
        max_human_players = 0 if requires_empty else 255
    if type(max_human_players) is not int or not 0 <= max_human_players <= 255:
        raise AutomationDenied("The occupancy limit is invalid.")
    if requires_empty:
        max_human_players = 0
    success, info = await a2s_cache_service.query_live_info(server)
    if success:
        if not isinstance(info, dict):
            raise AutomationDenied("The live player count is invalid.")
        players, bots = info.get("player_count"), info.get("bot_count")
        if type(players) is not int or type(bots) is not int or not 0 <= bots <= players <= 255:
            raise AutomationDenied("The live player count is invalid.")
        humans = players - bots
        if humans > max_human_players:
            raise AutomationDenied(
                "The server has human players. Automation was not executed."
                if max_human_players == 0
                else "The server has too many human players. Automation was not executed."
            )
        return
    if allow_stopped and not requires_online:
        await _confirm_stopped(server, manager)
        return
    raise AutomationDenied("A successful live A2S query is required for this game command.")
