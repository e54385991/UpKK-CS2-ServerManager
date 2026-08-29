"""Poll SteamCMD on the host after SSH reconnect so deploy logs keep moving.

The original stream can stall after "Waiting for user info...OK" (CR-only
progress) or die when the SSH session drops. A short watch on a fresh lease
publishes process counts and pane progress into the same operation log.
"""

from __future__ import annotations

import asyncio
import logging

from modules.models import Server
from services.deployment_progress import send_deployment_update
from services.game_session import (
    capture_console_command,
    find_running_session_manager,
    steamcmd_session_name,
)
from services.server_operation_hub import ACTIVE_STATUSES, server_operation_hub
from services.ssh_manager import SSHManager
from services.steamcmd_guard import steamcmd_pgrep_command
from services.steamcmd_session import incremental_console_lines, latest_console_heartbeat

logger = logging.getLogger(__name__)

WATCH_ACTIONS = frozenset({"deploy", "update", "validate"})
_WATCHES: set[int] = set()


async def maybe_resume_steamcmd_watch(server: Server) -> None:
    """Start one background poller if a SteamCMD operation is still running."""
    server_id = int(server.id)
    if server_id in _WATCHES:
        return
    record = await server_operation_hub.get_current(server_id)
    if record is None or record.get("status") not in ACTIVE_STATUSES:
        return
    if str(record.get("action") or "") not in WATCH_ACTIONS:
        return
    _WATCHES.add(server_id)
    try:
        await _run_watch(server)
    finally:
        _WATCHES.discard(server_id)


async def _run_watch(server: Server) -> None:
    server_id = int(server.id)
    ssh = SSHManager()
    success, message = await ssh.connect(server)
    if not success:
        await send_deployment_update(
            server_id,
            "info",
            f"SSH reconnected, but SteamCMD status cannot be polled: {message}",
        )
        return
    await send_deployment_update(
        server_id,
        "info",
        "SSH reconnected. Polling SteamCMD / download size so the log keeps updating.",
    )
    try:
        idle_empty = 0
        last_capture = ""
        name = steamcmd_session_name(server_id)
        for _ in range(40):
            record = await server_operation_hub.get_current(server_id)
            if record is None or record.get("status") not in ACTIVE_STATUSES:
                return
            manager = await find_running_session_manager(
                ssh.execute_command, server.session_manager, name, timeout=10
            )
            pids = await _list_pids(ssh, server)
            if manager:
                idle_empty = 0
                success, capture, _ = await ssh.execute_command(
                    capture_console_command(manager, name, lines=80),
                    timeout=15,
                )
                if success:
                    for line in incremental_console_lines(last_capture, capture or ""):
                        await send_deployment_update(server_id, "output", line)
                    last_capture = capture or last_capture
                    heartbeat = latest_console_heartbeat(capture or last_capture)
                    if heartbeat:
                        await send_deployment_update(server_id, "output", heartbeat)
                await send_deployment_update(
                    server_id,
                    "output",
                    f"SteamCMD {manager} session {name} running ({len(pids)} pid)",
                )
            elif pids:
                idle_empty = 0
                await send_deployment_update(
                    server_id,
                    "output",
                    f"SteamCMD still running ({len(pids)} pid)",
                )
            else:
                idle_empty += 1
                await send_deployment_update(
                    server_id,
                    "info",
                    f"SteamCMD session {name} not seen",
                )
                if idle_empty >= 3:
                    await send_deployment_update(
                        server_id,
                        "info",
                        "SteamCMD is not running on the host. If this deploy was "
                        "started before detached sessions, force-stop and deploy "
                        "again so SteamCMD starts in tmux/screen.",
                    )
                    return
            await asyncio.sleep(15)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("SteamCMD watch failed for server %s", server_id)
    finally:
        try:
            await ssh.disconnect()
        except Exception:
            logger.debug("SteamCMD watch disconnect failed", exc_info=True)


async def _list_pids(ssh: SSHManager, server: Server) -> list[str]:
    success, stdout, _stderr = await ssh.execute_command(
        steamcmd_pgrep_command(server.game_directory), timeout=10
    )
    if not success or not stdout.strip():
        return []
    return [pid for pid in stdout.strip().splitlines() if pid.isdigit()]
