"""Servers configuration endpoints."""

# ruff: noqa: F403,F405

from .common import *

discord_router = APIRouter(prefix="/servers", tags=["servers"])
custom_commands_router = APIRouter(prefix="/servers", tags=["servers"])
startup_router = APIRouter(prefix="/servers", tags=["servers"])


@discord_router.get("/{server_id}/discord-settings", response_model=DiscordSettingsResponse)
async def get_discord_settings(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get per-server Discord notification settings without exposing the webhook URL."""
    server = await get_server_with_permission(server_id, current_user, db)
    return build_discord_settings_response(server)


@discord_router.put("/{server_id}/discord-settings", response_model=DiscordSettingsResponse)
async def update_discord_settings(
    server_id: int,
    settings_data: DiscordSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Update per-server Discord notification settings."""
    server = await get_server_with_permission(server_id, current_user, db)
    update_data = settings_data.model_dump(exclude_unset=True)

    new_webhook = update_data.get("discord_webhook_url")
    if new_webhook:
        valid, error = discord_notification_service.validate_webhook_url(new_webhook)
        if not valid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)

    final_webhook = server.discord_webhook_url
    if settings_data.clear_webhook:
        final_webhook = None
    elif new_webhook:
        final_webhook = new_webhook

    final_enabled = update_data.get(
        "discord_notifications_enabled",
        server.discord_notifications_enabled,
    )
    if final_enabled and not final_webhook:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discord webhook URL is required before enabling notifications",
        )

    if settings_data.clear_webhook:
        server.discord_webhook_url = None
    elif new_webhook:
        server.discord_webhook_url = new_webhook

    if "discord_notifications_enabled" in update_data:
        server.discord_notifications_enabled = bool(update_data["discord_notifications_enabled"])
    if "discord_channel_name" in update_data:
        server.discord_channel_name = update_data["discord_channel_name"]
    if "discord_notify_auto_updates" in update_data:
        server.discord_notify_auto_updates = bool(update_data["discord_notify_auto_updates"])
    if "discord_notify_manual_updates" in update_data:
        server.discord_notify_manual_updates = bool(update_data["discord_notify_manual_updates"])
    if "discord_notify_plugin_updates" in update_data:
        server.discord_notify_plugin_updates = bool(update_data["discord_notify_plugin_updates"])
    if "discord_notify_s3_backups" in update_data:
        server.discord_notify_s3_backups = bool(update_data["discord_notify_s3_backups"])
    if "discord_notify_crash_restarts" in update_data:
        server.discord_notify_crash_restarts = bool(update_data["discord_notify_crash_restarts"])
    if "discord_crash_restart_min_interval_minutes" in update_data:
        server.discord_crash_restart_min_interval_minutes = int(
            update_data["discord_crash_restart_min_interval_minutes"] or 10
        )

    await db.commit()
    await db.refresh(server)
    await redis_manager.clear_server_cache(server_id)

    return build_discord_settings_response(server)


@discord_router.post("/{server_id}/discord-settings/test")
async def test_discord_settings(
    server_id: int,
    request: DiscordTestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Send a Discord test notification using the saved webhook."""
    server = await get_server_with_permission(server_id, current_user, db)
    success, message = await discord_notification_service.send_test(server, request.message)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return {"success": True, "message": message}


@custom_commands_router.get(
    "/{server_id}/custom-commands", response_model=List[CustomCommandResponse]
)
async def list_custom_commands(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """List saved quick commands for this server and current user"""
    server = await get_server_with_permission(server_id, current_user, db)
    return await CustomCommand.get_all_by_server_and_user(db, server.id, current_user.id)


@custom_commands_router.post(
    "/{server_id}/custom-commands",
    response_model=CustomCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_custom_command(
    server_id: int,
    command_data: CustomCommandCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Create a saved quick command for this server"""
    server = await get_server_with_permission(server_id, current_user, db)
    custom_command = CustomCommand(
        user_id=current_user.id,
        server_id=server.id,
        name=command_data.name,
        target=command_data.target,
        commands=command_data.commands,
    )
    db.add(custom_command)
    await db.commit()
    await db.refresh(custom_command)
    return custom_command


@custom_commands_router.put(
    "/{server_id}/custom-commands/{command_id}", response_model=CustomCommandResponse
)
async def update_custom_command(
    server_id: int,
    command_id: int,
    command_data: CustomCommandUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Update a saved quick command"""
    server = await get_server_with_permission(server_id, current_user, db)
    custom_command = await get_custom_command_or_404(db, server.id, command_id, current_user)

    update_data = command_data.model_dump(exclude_unset=True)
    custom_command.sqlmodel_update(update_data)

    db.add(custom_command)
    await db.commit()
    await db.refresh(custom_command)
    return custom_command


@custom_commands_router.delete(
    "/{server_id}/custom-commands/{command_id}", response_model=ActionResponse
)
async def delete_custom_command(
    server_id: int,
    command_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Delete a saved quick command"""
    server = await get_server_with_permission(server_id, current_user, db)
    custom_command = await get_custom_command_or_404(db, server.id, command_id, current_user)
    await db.delete(custom_command)
    await db.commit()
    return ActionResponse(success=True, message="Custom command deleted successfully")


@custom_commands_router.post("/{server_id}/custom-commands/execute", response_model=ActionResponse)
async def execute_one_time_custom_command(
    server_id: int,
    command_data: CustomCommandExecuteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Execute one-time custom commands without saving them"""
    server = await get_server_with_permission(server_id, current_user, db)
    result = await execute_and_log_custom_commands(
        db,
        server,
        command_data.target,
        command_data.commands,
    )
    return ActionResponse(success=result["success"], message=result["message"], data=result)


@custom_commands_router.post(
    "/{server_id}/custom-commands/{command_id}/execute", response_model=ActionResponse
)
async def execute_saved_custom_command(
    server_id: int,
    command_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Execute a saved quick command"""
    server = await get_server_with_permission(server_id, current_user, db)
    custom_command = await get_custom_command_or_404(db, server.id, command_id, current_user)
    result = await execute_and_log_custom_commands(
        db,
        server,
        custom_command.target,
        custom_command.commands,
        name=custom_command.name,
    )
    return ActionResponse(success=result["success"], message=result["message"], data=result)


@startup_router.get("/{server_id}/startup-command")
async def get_startup_command(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Generate a preview of the startup command based on current server settings.
    This mirrors the logic in ssh_manager.start_server() but does not require SSH.
    Sensitive values (passwords, tokens) are masked.
    """
    server = await get_server_with_permission(server_id, current_user, db)

    # Config with safe defaults (mirrors start_server logic)
    default_map = server.default_map or "de_dust2"
    max_players = server.max_players or 32
    server_name = server.server_name or f"CS2 Server {server.id}"

    # Game mode mapping
    game_mode_str = server.game_mode or "competitive"
    mode_mapping = {
        "casual": ("0", "0"),
        "competitive": ("0", "1"),
        "wingman": ("0", "2"),
        "arms_race": ("1", "0"),
        "armsrace": ("1", "0"),
        "demolition": ("1", "1"),
        "deathmatch": ("2", "0"),
        "custom": ("3", "0"),
    }

    if game_mode_str:
        game_mode_lower = game_mode_str.lower()
        if game_mode_lower in mode_mapping:
            mapped_game_type, mapped_game_mode = mode_mapping[game_mode_lower]
            game_mode = mapped_game_mode
            game_type = server.game_type if server.game_type else mapped_game_type
        elif game_mode_str.isdigit():
            game_mode = game_mode_str
            game_type = server.game_type or "0"
        else:
            game_mode = "1"
            game_type = "0"
    else:
        game_mode = "1"
        game_type = server.game_type or "0"

    # Build parameters (same as start_server)
    params = [
        "-dedicated",
        f"-port {server.game_port}",
        f"+map {default_map}",
        f"-maxplayers {max_players}",
        f'+hostname "{server_name}"',
    ]

    if server.ip_address:
        params.append(f"-ip {server.ip_address}")

    if server.client_port:
        params.append(f"+clientport {server.client_port}")
    elif server.game_port:
        params.append(f"+clientport {server.game_port + 1}")

    gslt_parameter = gslt_startup_parameter(
        server.steam_account_token,
        masked=True,
    )
    if gslt_parameter:
        params.append(gslt_parameter)

    if server.server_password:
        params.append('+sv_password "***PASSWORD***"')

    if server.rcon_password:
        params.append('+rcon_password "***RCON_PASSWORD***"')

    params.append(f"+game_mode {game_mode}")
    params.append(f"+game_type {game_type}")

    if server.tv_enable and server.tv_port:
        params.extend(
            [
                "+tv_enable 1",
                f"+tv_port {server.tv_port}",
                '+tv_name "GOTV"',
            ]
        )

    if server.additional_parameters:
        params.append(server.additional_parameters.strip())

    params_str = " ".join(params)

    # Build paths
    game_bin_dir = f"{server.game_directory}/cs2/game/bin/linuxsteamrt64"
    cs2_executable = "./cs2"

    cs2_start_cmd = (
        f"cd {game_bin_dir} && "
        f"export LD_LIBRARY_PATH='{game_bin_dir}:${{LD_LIBRARY_PATH}}' && "
        f"{cs2_executable} {params_str}"
    )

    # CPU affinity is applied to the process inside the detached session.
    cpu_affinity = None
    if server.cpu_affinity and re.match(r"^[\d,\-\s]+$", server.cpu_affinity.strip()):
        cpu_affinity = server.cpu_affinity.strip()

    # Build full command with the configured session manager.
    autorestart_script_path = f"{server.game_directory}/cs2_autorestart.sh"
    api_key = server.api_key or ""
    backend_url = server.backend_url or app_settings.BACKEND_URL
    manager = normalize_session_manager(server.session_manager)
    name = session_name(server.id)

    if api_key:
        payload = (
            f"bash {shlex.quote(autorestart_script_path)} "
            f"{server.id} {shlex.quote('***API_KEY***')} "
            f"{shlex.quote(backend_url)} {shlex.quote(server.game_directory)} "
            f"{shlex.quote(cs2_start_cmd)}"
        )
        start_cmd = start_session_command(manager, name, payload, cpu_affinity)
    else:
        console_log = f"{server.game_directory}/cs2/game/csgo/console.log"
        shell_payload = (
            f"cd {shlex.quote(game_bin_dir)} && "
            f'export LD_LIBRARY_PATH={shlex.quote(game_bin_dir)}:"${{LD_LIBRARY_PATH:-}}" && '
            f"{cs2_executable} {params_str} 2>&1 | tee {shlex.quote(console_log)}"
        )
        payload = f"bash -c {shlex.quote(shell_payload)}"
        start_cmd = start_session_command(manager, name, payload, cpu_affinity)

    return {
        "startup_command": start_cmd,
        "cs2_command": f"{cs2_executable} {params_str}",
        "session_manager": manager,
        "game_mode_resolved": f"{game_mode_str} (game_type: {game_type}, game_mode: {game_mode})",
    }


router = APIRouter()
for _router in (discord_router, custom_commands_router, startup_router):
    router.include_router(_router)
