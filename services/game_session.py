"""Commands for managing detached CS2 console sessions.

The panel supports both GNU screen and tmux.  Keeping command construction in
one place prevents the start/stop/status/console paths from drifting apart.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Awaitable, Callable
from typing import Any

DEFAULT_SESSION_MANAGER = "tmux"
SUPPORTED_SESSION_MANAGERS = ("screen", "tmux")
TMUX_SOCKET_NAME = "upkk-cs2"

ExecuteCommand = Callable[..., Awaitable[tuple[bool, str, str]]]


def normalize_session_manager(value: Any) -> str:
    """Return a supported manager name, falling back to the current default."""
    if hasattr(value, "session_manager"):
        value = value.session_manager
    manager = str(value or DEFAULT_SESSION_MANAGER).strip().lower()
    return manager if manager in SUPPORTED_SESSION_MANAGERS else DEFAULT_SESSION_MANAGER


def gslt_startup_parameter(value: Any, *, masked: bool = False) -> str | None:
    """Return a safe GSLT startup parameter, or ``None`` when unconfigured."""
    token = str(value or "").strip()
    if not token:
        return None
    if not re.fullmatch(r"[A-Za-z0-9]+", token):
        raise ValueError("Steam account token must only contain alphanumeric characters")
    rendered_token = "***STEAM_TOKEN***" if masked else token
    return f'+sv_setsteamaccount "{rendered_token}"'


def cs2_startup_parameters(
    *,
    game_port: int,
    default_map: Any,
    max_players: int,
    server_name: Any,
    ip_address: Any = None,
    client_port: int | None = None,
    steam_account_token: Any = None,
    server_password: Any = None,
    rcon_password: Any = None,
    game_mode: Any = "1",
    game_type: Any = "0",
    tv_enable: bool = False,
    tv_port: int | None = None,
    additional_parameters: Any = None,
    masked: bool = False,
) -> str:
    """Build shell-safe CS2 arguments while preserving custom argv semantics.

    Values are represented as individual argv entries and shell-quoted only at
    the final boundary.  Advanced parameters are parsed as arguments instead
    of being appended as executable shell source, so separators and command
    substitutions remain literal values.
    """
    arguments = [
        "-dedicated",
        "-port",
        str(int(game_port)),
        "+map",
        str(default_map or "de_dust2"),
        "-maxplayers",
        str(int(max_players)),
        "+hostname",
        str(server_name or "CS2 Server"),
    ]

    if ip_address:
        arguments.extend(("-ip", str(ip_address)))
    if client_port:
        arguments.extend(("+clientport", str(int(client_port))))
    elif game_port:
        arguments.extend(("+clientport", str(int(game_port) + 1)))

    token = str(steam_account_token or "").strip()
    if token:
        if not re.fullmatch(r"[A-Za-z0-9]+", token):
            raise ValueError("Steam account token must only contain alphanumeric characters")
        arguments.extend(("+sv_setsteamaccount", "***STEAM_TOKEN***" if masked else token))

    if server_password:
        arguments.extend(("+sv_password", "***PASSWORD***" if masked else str(server_password)))
    if rcon_password:
        arguments.extend(
            ("+rcon_password", "***RCON_PASSWORD***" if masked else str(rcon_password))
        )

    arguments.extend(("+game_mode", str(game_mode), "+game_type", str(game_type)))
    if tv_enable and tv_port:
        arguments.extend(("+tv_enable", "1", "+tv_port", str(int(tv_port)), "+tv_name", "GOTV"))

    custom = str(additional_parameters or "").strip()
    if custom:
        try:
            arguments.extend(shlex.split(custom, posix=True))
        except ValueError as exc:
            raise ValueError(f"Invalid additional parameters: {exc}") from exc

    return shlex.join(arguments)


def session_manager_order(preferred: Any) -> tuple[str, ...]:
    """Check the configured manager first, then the other manager for migrations."""
    manager = normalize_session_manager(preferred)
    return (manager,) + tuple(item for item in SUPPORTED_SESSION_MANAGERS if item != manager)


def session_name(server_id: int) -> str:
    return f"cs2server_{int(server_id)}"


def _safe_session_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("Invalid session name")
    return name


def availability_command(manager: Any) -> str:
    manager = normalize_session_manager(manager)
    return f"command -v {manager} >/dev/null 2>&1"


def _tmux() -> str:
    # A dedicated socket and empty config isolate game sessions from the SSH
    # user's personal tmux server and options.
    return f"tmux -L {TMUX_SOCKET_NAME} -f /dev/null"


def session_exists_command(manager: Any, name: str) -> str:
    manager = normalize_session_manager(manager)
    name = _safe_session_name(name)
    if manager == "tmux":
        return f"{_tmux()} has-session -t {shlex.quote('=' + name)} 2>/dev/null"

    # Match the complete screen session name so cs2server_1 cannot match
    # cs2server_10.  screen prints sessions as "PID.session_name (...)".
    pattern = rf"[.]{re.escape(name)}([[:space:]]|$)"
    return f"screen -list 2>/dev/null | grep -E {shlex.quote(pattern)} >/dev/null"


def cleanup_command(manager: Any) -> str | None:
    """Return manager-specific stale-session cleanup, if one is needed."""
    return (
        "screen -wipe >/dev/null 2>&1 || true"
        if normalize_session_manager(manager) == "screen"
        else None
    )


def start_session_command(
    manager: Any,
    name: str,
    payload_command: str,
    cpu_affinity: str | None = None,
) -> str:
    """Wrap a process command in the selected detached session manager.

    CPU affinity is deliberately applied to the payload.  Prefixing the tmux
    client would not constrain the process when an existing tmux server owns
    the newly-created session.
    """
    manager = normalize_session_manager(manager)
    name = _safe_session_name(name)
    payload = payload_command
    if cpu_affinity:
        affinity = cpu_affinity.strip()
        if not re.fullmatch(r"[\d,\-\s]+", affinity):
            raise ValueError("Invalid CPU affinity")
        payload = f"taskset -c {shlex.quote(affinity)} {payload}"

    if manager == "tmux":
        return f"{_tmux()} new-session -d -s {shlex.quote(name)} {payload}"
    return f"screen -dmS {shlex.quote(name)} {payload}"


def stop_session_command(manager: Any, name: str) -> str:
    manager = normalize_session_manager(manager)
    name = _safe_session_name(name)
    if manager == "tmux":
        return f"{_tmux()} kill-session -t {shlex.quote('=' + name)}"
    return f"screen -S {shlex.quote(name)} -X quit"


def force_stop_session_command(manager: Any, name: str) -> str:
    """Force only the requested session; never kill the shared tmux server."""
    manager = normalize_session_manager(manager)
    name = _safe_session_name(name)
    if manager == "tmux":
        return f"{_tmux()} kill-session -t {shlex.quote('=' + name)} 2>/dev/null || true"
    # [S]CREEN matches the target argv but not the pkill command line itself.
    pattern = shlex.quote(rf"[S]CREEN.*[.]?{re.escape(name)}([[:space:]]|$)")
    return f"pkill -9 -f {pattern} 2>/dev/null || true"


def send_keys_command(manager: Any, name: str, command: str) -> str:
    manager = normalize_session_manager(manager)
    name = _safe_session_name(name)
    if manager == "tmux":
        # send-keys expects a pane target.  The trailing colon selects the
        # active pane in the exactly-matched session.
        target = shlex.quote("=" + name + ":")
        literal = shlex.quote(command)
        return (
            f"{_tmux()} send-keys -t {target} -l -- {literal} && "
            f"{_tmux()} send-keys -t {target} Enter"
        )
    return f"screen -S {shlex.quote(name)} -X stuff {shlex.quote(command + chr(10))}"


def attach_command(manager: Any, name: str) -> str:
    manager = normalize_session_manager(manager)
    name = _safe_session_name(name)
    if manager == "tmux":
        return f"{_tmux()} attach-session -t {shlex.quote('=' + name)}"
    return f"screen -x {shlex.quote(name)}"


async def find_running_session_managers(
    execute_command: ExecuteCommand,
    preferred: Any,
    name: str,
    *,
    timeout: int = 10,
) -> list[str]:
    """Return all matching managers, checking the configured one first."""
    running: list[str] = []
    for manager in session_manager_order(preferred):
        success, _, _ = await execute_command(
            session_exists_command(manager, name), timeout=timeout
        )
        if success:
            running.append(manager)
    return running


async def find_running_session_manager(
    execute_command: ExecuteCommand,
    preferred: Any,
    name: str,
    *,
    timeout: int = 10,
) -> str | None:
    managers = await find_running_session_managers(
        execute_command, preferred, name, timeout=timeout
    )
    return managers[0] if managers else None
