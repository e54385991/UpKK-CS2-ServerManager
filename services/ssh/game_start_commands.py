"""Focused lifecycle stages with explicit session inputs."""

# ruff: noqa: F403,F405

from services.compat import LateBoundModule

from .common import *

host = LateBoundModule("services.ssh.game_start")


def _build_startup_parameters(server: Server) -> tuple[str, str, str, str, str, int]:
    cs2_executable = "./cs2"  # Use relative path when in correct directory

    # Get configuration with safe defaults
    default_map = host.normalize_default_map(server.default_map or "de_dust2")
    game_mode_str = server.game_mode or "competitive"
    game_type, game_mode = host.resolved_game_mode(game_mode_str, server.game_type)
    additional_parameters = host.normalize_additional_parameters(server.additional_parameters)
    max_players = server.max_players or 32
    server_name = server.server_name or f"CS2 Server {server.id}"

    # Core parameters
    # Note: -tickrate is no longer supported in CS2
    params = [
        "-dedicated",
        f"-port {server.game_port}",
        f"+map {default_map}",
        f"-maxplayers {max_players}",
        f'+hostname "{server_name}"',
    ]

    # Optional IP binding
    if server.ip_address:
        params.append(f"-ip {server.ip_address}")

    # Client port (usually game_port + 1)
    if server.client_port:
        params.append(f"+clientport {server.client_port}")
    elif server.game_port:
        params.append(f"+clientport {server.game_port + 1}")

    # Steam account token (GSLT) - required only for public servers
    gslt_parameter = host.gslt_startup_parameter(server.steam_account_token)
    if gslt_parameter:
        params.append(gslt_parameter)

    # Server password
    if server.server_password:
        params.append(f'+sv_password "{server.server_password}"')

    # RCON password
    if server.rcon_password:
        params.append(f'+rcon_password "{server.rcon_password}"')

    # Game mode and type
    params.append(f"+game_mode {game_mode}")
    params.append(f"+game_type {game_type}")

    # SourceTV configuration
    if server.tv_enable and server.tv_port:
        params.extend(
            [
                "+tv_enable 1",
                f"+tv_port {server.tv_port}",
                '+tv_name "GOTV"',
            ]
        )

    # Additional custom parameters
    if additional_parameters:
        params.append(additional_parameters)

    # Combine all parameters
    params_str = " ".join(params)
    return params_str, default_map, game_mode_str, game_type, game_mode, max_players


async def _prepare_start_wrapper(
    self: SSHMixinBase, server: Server, send_progress
) -> tuple[bool, str]:
    autorestart_script_path = f"{server.game_directory}/cs2_autorestart.sh"
    try:
        script_ready, script_status = await host.ensure_autorestart_script(
            self.execute_command,
            autorestart_script_path,
        )
    except Exception as e:
        script_ready, script_status = False, str(e)
    if not script_ready:
        await send_progress(f"⚠ Warning: Could not deploy autorestart script: {script_status}")
        await send_progress("Server will start without auto-restart protection")
        use_autorestart = False
    elif script_status == "current":
        await send_progress("✓ Auto-restart script found")
        use_autorestart = True
    elif script_status.startswith("refresh failed"):
        await send_progress(f"⚠ Warning: {script_status}")
        await send_progress("✓ Auto-restart script found")
        use_autorestart = True
    else:
        await send_progress("✓ Auto-restart wrapper script deployed")
        use_autorestart = True
    return use_autorestart, autorestart_script_path


async def _build_start_command(
    server: Server,
    manager: str,
    name: str,
    params_str: str,
    cs2_executable: str,
    use_autorestart: bool,
    api_key: str,
    backend_url: str,
    autorestart_script_path: str,
    send_progress,
) -> str:
    game_bin_dir = f"{server.game_directory}/cs2/game/bin/linuxsteamrt64"

    # Build the CS2 command independently of its session manager.
    cs2_start_cmd = (
        f"cd {host.shlex.quote(game_bin_dir)} && "
        f"export LD_LIBRARY_PATH={host.shlex.quote(game_bin_dir)}:"
        f'"${{LD_LIBRARY_PATH:-}}" && '
        f"{cs2_executable} {params_str}"
    )

    # CPU affinity belongs to the payload, not the tmux client.  A
    # long-lived tmux daemon would otherwise retain its old affinity.
    cpu_affinity = None
    if server.cpu_affinity:
        affinity = server.cpu_affinity.strip()
        if host.re.fullmatch(r"[\d,\-\s]+", affinity):
            cpu_affinity = affinity
            await send_progress(f"✓ CPU affinity configured: cores {affinity}")
        else:
            await send_progress(
                f"⚠ Warning: Invalid CPU affinity format '{server.cpu_affinity}', ignoring"
            )

    if use_autorestart and api_key:
        protection_seconds = int(host.restart_protection_window(server).total_seconds())
        payload = (
            f"TIME_WINDOW={protection_seconds} "
            f"bash {host.shlex.quote(autorestart_script_path)} "
            f"{server.id} {host.shlex.quote(api_key)} "
            f"{host.shlex.quote(backend_url)} {host.shlex.quote(server.game_directory)} "
            f"{host.shlex.quote(cs2_start_cmd)}"
        )
        start_cmd = host.start_session_command(
            manager,
            name,
            payload,
            cpu_affinity,
        )
        await send_progress("✓ Starting with auto-restart protection enabled")
    else:
        console_log = f"{server.game_directory}/cs2/game/csgo/console.log"
        shell_payload = (
            f"cd {host.shlex.quote(game_bin_dir)} && "
            f"export LD_LIBRARY_PATH={host.shlex.quote(game_bin_dir)}:"
            f'"${{LD_LIBRARY_PATH:-}}" && '
            f"{cs2_executable} {params_str} 2>&1 | "
            f"tee {host.shlex.quote(console_log)}"
        )
        payload = f"bash -c {host.shlex.quote(shell_payload)}"
        start_cmd = host.start_session_command(
            manager,
            name,
            payload,
            cpu_affinity,
        )
        if not api_key:
            await send_progress("⚠ Warning: No API key configured, auto-restart reporting disabled")
    return start_cmd


def sanitize_sensitive_value(cmd: str, value: str, replacement: str) -> str:
    """
    Helper to sanitize a sensitive value from a command string.
    Handles single quotes, double quotes, and unquoted occurrences.
    Processes quoted occurrences first, then unquoted to avoid partial exposure.
    Uses regex escaping to handle special characters safely.
    """
    if value is None or value.strip() == "":
        return cmd
    # Escape the value for safe string replacement (handles special characters)
    escaped_value = host.re.escape(value)
    # Replace quoted occurrences first (more specific matches)
    cmd = host.re.sub(f'"{escaped_value}"', f'"{replacement}"', cmd)
    cmd = host.re.sub(f"'{escaped_value}'", f"'{replacement}'", cmd)
    # Replace unquoted occurrences last (more general match)
    cmd = host.re.sub(escaped_value, replacement, cmd)
    return cmd


async def _report_start_command(
    server: Server, start_cmd: str, api_key: str, send_progress
) -> None:
    sanitized_cmd = start_cmd
    sanitized_cmd = host.sanitize_sensitive_value(sanitized_cmd, api_key, "***API_KEY***")
    if server.server_password:
        sanitized_cmd = host.sanitize_sensitive_value(
            sanitized_cmd, server.server_password, "***PASSWORD***"
        )
    if server.rcon_password:
        sanitized_cmd = host.sanitize_sensitive_value(
            sanitized_cmd, server.rcon_password, "***RCON_PASSWORD***"
        )
    normalized_gslt = (server.steam_account_token or "").strip()
    sanitized_cmd = host.sanitize_sensitive_value(
        sanitized_cmd,
        normalized_gslt,
        "***STEAM_TOKEN***",
    )
    await send_progress(sanitized_cmd)
