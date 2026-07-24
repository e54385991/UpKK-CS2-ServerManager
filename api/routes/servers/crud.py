"""Servers crud endpoints."""

# ruff: noqa: F403,F405

import hashlib

from .common import *

router = APIRouter(prefix="/servers", tags=["servers"])


@router.post(
    "",
    response_model=ServerCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ErrorResponse},
    },
)
async def create_server(
    server_data: ServerCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Create a new CS2 server"""
    # Check if server name already exists for this user
    existing = await Server.get_by_name_and_user(db, server_data.name, current_user.id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Server with name '{server_data.name}' already exists",
        )

    # Check if server with same host and game_directory already exists for this user
    duplicate_server = await Server.get_by_host_directory_and_user(
        db, server_data.host, server_data.game_directory, current_user.id
    )
    if duplicate_server:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A server with the same host ({server_data.host}) and game directory ({server_data.game_directory}) already exists. "
            f"If you want to add a new server on this host, please use a different game directory or manually delete the existing directory on the server first.",
        )

    await db.commit()

    # Discover the public host identity without sending credentials. Creation
    # proceeds only when the user submits this exact, freshly-scanned key.
    try:
        scanned_host_key = await scan_ssh_host_key(server_data.host, server_data.ssh_port)
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="SSH host-key scan timed out",
        ) from None
    except (OSError, asyncssh.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unable to scan SSH host key: {exc}",
        ) from exc

    confirmed_host_key = (
        server_data.ssh_host_key_confirmed
        and server_data.ssh_host_key_algorithm == scanned_host_key.algorithm
        and server_data.ssh_host_key_fingerprint == scanned_host_key.fingerprint
    )
    if not confirmed_host_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ssh_host_key_confirmation_required",
                "message": "Confirm the SSH host key before credentials are sent",
                "algorithm": scanned_host_key.algorithm,
                "fingerprint": scanned_host_key.fingerprint,
            },
        )
    host_key_options = pinned_host_key_options(
        scanned_host_key.algorithm,
        scanned_host_key.fingerprint,
    )

    # Consume the one-time CAPTCHA only after host-key confirmation, allowing
    # the scan/confirm round trip to reuse the original form submission.
    is_valid = await captcha_service.validate_captcha(
        server_data.captcha_token, server_data.captcha_code
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired CAPTCHA code"
        )

    # Validate SSH connection before creating server (password authentication only)
    conn = None
    try:
        if not server_data.ssh_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="SSH password is required"
            )

        # Step 1: Attempt SSH connection
        try:
            conn = await asyncssh.connect(
                server_data.host,
                port=server_data.ssh_port,
                username=server_data.ssh_user,
                password=server_data.ssh_password,
                connect_timeout=15,
                **host_key_options,
            )
        except asyncssh.PermissionDenied:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SSH authentication failed for {server_data.ssh_user}@{server_data.host}. Please verify your username and password.",
            ) from None
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"SSH connection to {server_data.host}:{server_data.ssh_port} timed out. The server may be unreachable or too slow to respond. Please check the network connection and server status.",
            ) from None
        except asyncssh.ConnectionLost:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Connection to {server_data.host}:{server_data.ssh_port} was lost. Please check if the server is reachable.",
            ) from None
        except asyncssh.Error as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SSH connection to {server_data.host}:{server_data.ssh_port} failed: {str(e)}. Please verify the host and port.",
            ) from e

        # Step 2: Test command execution
        result = await conn.run("echo 'SSH connection successful'", check=False)

        if result.exit_status != 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SSH connection succeeded but command execution failed. Please verify that user {server_data.ssh_user} has proper shell access and permissions.",
            )

        # Step 3: Create game directory with proper permissions
        # Use shlex.quote to safely escape the directory path
        game_dir_quoted = shlex.quote(server_data.game_directory)
        mkdir_cmd = f"mkdir -p {game_dir_quoted}"

        result = await conn.run(mkdir_cmd, check=False)
        if result.exit_status != 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to create game directory {server_data.game_directory}. Please check permissions and path.",
            )

        # Set proper permissions (755 - owner can read/write/execute, others can read/execute)
        chmod_cmd = f"chmod 755 {game_dir_quoted}"
        result = await conn.run(chmod_cmd, check=False)
        if result.exit_status != 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to set permissions on game directory {server_data.game_directory}. Please check user permissions.",
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to validate server connection: {str(e)}",
        ) from e
    finally:
        # Ensure connection is always closed
        if conn:
            conn.close()

    # Create server with user_id, auto-generated API key, and password auth
    # Exclude protocol-only confirmation fields from persistence.
    server_dict = server_data.model_dump(
        exclude={"captcha_token", "captcha_code", "ssh_host_key_confirmed"}
    )
    server_dict["auth_type"] = AuthType.PASSWORD  # Always use password authentication

    # Apply system default proxy settings if not explicitly set by user
    system_settings = await SystemSettings.get_or_create_settings(db)
    # If user hasn't explicitly set proxy mode, apply system defaults.
    if not server_dict.get("use_panel_proxy") and not server_dict.get("github_proxy"):
        if system_settings.default_proxy_mode == "panel":
            server_dict["use_panel_proxy"] = True
            server_dict["github_proxy"] = None
        elif (
            system_settings.default_proxy_mode == "github_url" and system_settings.github_proxy_url
        ):
            server_dict["use_panel_proxy"] = False
            server_dict["github_proxy"] = system_settings.github_proxy_url
        # else: default_proxy_mode is 'direct', keep both as None/False

    one_time_api_key = generate_api_key()
    server = Server(**server_dict, user_id=current_user.id)
    server.set_api_key(one_time_api_key)
    db.add(server)
    await db.flush()
    for default_path in DEFAULT_PLUGIN_CONFIG_SOURCE_PATHS:
        db.add(
            PluginConfigSource(
                server_id=server.id,
                relative_path=default_path,
                path_hash=hashlib.sha256(default_path.encode("utf-8")).hexdigest(),
                source_type="directory",
                is_default=True,
                is_enabled=True,
            )
        )
    await db.commit()
    await db.refresh(server)

    response.headers["Cache-Control"] = "no-store"
    server_response = ServerResponse.model_validate(server).model_dump()
    return ServerCreatedResponse(**server_response, api_key=one_time_api_key)


@router.post(
    "/ssh-host-key/scan",
    response_model=SSHHostKeyResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ErrorResponse},
    },
)
async def scan_new_server_host_key(
    request_data: SSHHostKeyScanRequest,
    _current_user: User = Depends(get_current_active_user),
):
    """Scan a prospective server without sending authentication credentials."""
    try:
        identity = await scan_ssh_host_key(request_data.host, request_data.ssh_port)
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="SSH host-key scan timed out",
        ) from None
    except (OSError, asyncssh.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unable to scan SSH host key: {exc}",
        ) from exc
    return SSHHostKeyResponse(
        algorithm=identity.algorithm,
        fingerprint=identity.fingerprint,
        configured=False,
    )


@router.post(
    "/{server_id}/ssh-host-key/scan",
    response_model=SSHHostKeyResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ErrorResponse},
    },
)
async def scan_existing_server_host_key(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Show the live key of an existing server for an explicit trust decision."""
    server = await get_server_with_permission(server_id, current_user, db)
    await db.commit()
    try:
        identity = await scan_ssh_host_key(server.host, server.ssh_port)
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="SSH host-key scan timed out",
        ) from None
    except (OSError, asyncssh.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unable to scan SSH host key: {exc}",
        ) from exc
    configured = bool(server.ssh_host_key_algorithm and server.ssh_host_key_fingerprint)
    return SSHHostKeyResponse(
        algorithm=identity.algorithm,
        fingerprint=identity.fingerprint,
        configured=configured,
        matches_configured=(
            identity.algorithm == server.ssh_host_key_algorithm
            and identity.fingerprint == server.ssh_host_key_fingerprint
            if configured
            else None
        ),
    )


@router.post(
    "/{server_id}/ssh-host-key/confirm",
    response_model=ServerDetail,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ErrorResponse},
    },
)
async def confirm_existing_server_host_key(
    server_id: int,
    confirmation: SSHHostKeyConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Re-scan and atomically pin the exact key confirmed by the user."""
    server = await get_server_with_permission(server_id, current_user, db)
    await db.commit()
    try:
        identity = await scan_ssh_host_key(server.host, server.ssh_port)
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="SSH host-key scan timed out",
        ) from None
    except (OSError, asyncssh.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unable to scan SSH host key: {exc}",
        ) from exc
    if (
        confirmation.algorithm != identity.algorithm
        or confirmation.fingerprint != identity.fingerprint
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="SSH host key changed after it was scanned; review the new fingerprint",
        )
    server.ssh_host_key_algorithm = identity.algorithm
    server.ssh_host_key_fingerprint = identity.fingerprint
    server.credential_revision = (server.credential_revision or 0) + 1
    await db.commit()
    await db.refresh(server)
    return server


@router.get("", response_model=List[ServerSummary])
async def list_servers(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """List all servers owned by current user"""
    servers = await Server.get_all_by_user(db, current_user.id, skip, limit)
    return servers


@router.get("/admin/all", response_model=List[ServerResponseWithUser])
async def list_all_servers_admin(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """List all servers across all users (admin only)"""
    servers = await Server.get_all(db, skip, limit)

    # Early return if no servers
    if not servers:
        return []

    # Fetch all unique user IDs and load users in one query to avoid N+1
    user_ids = {server.user_id for server in servers}
    users_result = await db.execute(select(User).where(User.id.in_(user_ids)))
    users = {user.id: user for user in users_result.scalars().all()}

    # Build response with user information
    result = []
    for server in servers:
        server_dict = ServerResponse.model_validate(server).model_dump()
        user = users.get(server.user_id)
        server_dict["user"] = UserResponse.model_validate(user) if user else None
        result.append(ServerResponseWithUser(**server_dict))

    return result


@router.get("/{server_id}", response_model=ServerDetail)
async def get_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get server by ID - admins can access any server, users can only access their own"""
    server = await get_server_with_permission(server_id, current_user, db)
    return server


@router.put("/{server_id}", response_model=ServerDetail)
async def update_server(
    server_id: int,
    server_data: ServerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Update server - admins can update any server, users can only update their own"""
    server = await get_server_with_permission(server_id, current_user, db)

    # Fields that affect the startup command - changes require server restart to take effect
    startup_affecting_fields = {
        "game_port",
        "game_directory",
        "server_name",
        "default_map",
        "max_players",
        "game_mode",
        "game_type",
        "server_password",
        "rcon_password",
        "steam_account_token",
        "tv_enable",
        "tv_port",
        "additional_parameters",
        "ip_address",
        "client_port",
        "cpu_affinity",
        "session_manager",
    }

    # Track old values for startup-affecting fields
    old_values = {}
    for field in startup_affecting_fields:
        if hasattr(server, field):
            old_values[field] = getattr(server, field)

    # Track if monitoring status changed
    old_monitoring_enabled = server.enable_panel_monitoring

    # Update fields using SQLModel's sqlmodel_update method
    update_data = server_data.model_dump(exclude_unset=True)
    endpoint_changed = any(
        field in update_data and getattr(server, field) != update_data[field]
        for field in ("host", "ssh_port")
    )
    connection_credential_fields = {
        "host",
        "ssh_port",
        "ssh_user",
        "auth_type",
        "ssh_password",
        "ssh_key_path",
        "sudo_password",
        "ssh_host_key_algorithm",
        "ssh_host_key_fingerprint",
    }
    credentials_changed = any(
        field in update_data and getattr(server, field, None) != update_data[field]
        for field in connection_credential_fields
    )
    for key, value in update_data.items():
        setattr(server, key, value)
    if endpoint_changed:
        # A pin belongs to one network endpoint. Moving the record requires a
        # fresh confirmation before any credentials can be sent.
        server.ssh_host_key_algorithm = None
        server.ssh_host_key_fingerprint = None
    if credentials_changed:
        server.credential_revision = (server.credential_revision or 0) + 1

    # Check if any startup-affecting fields changed while server is running
    restart_required = False
    if server.status == ServerStatus.RUNNING:
        for field in startup_affecting_fields:
            if field in update_data and old_values.get(field) != getattr(server, field):
                restart_required = True
                break

    await db.commit()
    await db.refresh(server)

    # Handle monitoring status change
    from services.server_monitor import server_monitor
    from services.ssh_manager import SSHManager

    new_monitoring_enabled = server.enable_panel_monitoring

    if new_monitoring_enabled and not old_monitoring_enabled:
        # Monitoring was enabled - start monitoring
        ssh_manager = SSHManager()
        server_monitor.start_monitoring(server_id, ssh_manager)
    elif not new_monitoring_enabled and old_monitoring_enabled:
        # Monitoring was disabled - stop monitoring
        server_monitor.stop_monitoring(server_id)

    # Clear cache
    await redis_manager.clear_server_cache(server_id)

    # Build response with restart_required flag
    response = ServerResponse.model_validate(server)
    response.restart_required = restart_required
    return response


@router.post("/{server_id}/apply-system-defaults", response_model=ServerDetail)
async def apply_system_defaults_to_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Apply system default proxy settings to a server"""
    server = await get_server_with_permission(server_id, current_user, db)

    # Get system settings
    system_settings = await SystemSettings.get_settings(db)
    if not system_settings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="System settings not configured"
        )

    # Apply system default proxy mode
    if system_settings.default_proxy_mode == "panel":
        server.use_panel_proxy = True
        server.github_proxy = None
    elif system_settings.default_proxy_mode == "github_url":
        server.use_panel_proxy = False
        server.github_proxy = system_settings.github_proxy_url
    else:  # 'direct'
        server.use_panel_proxy = False
        server.github_proxy = None

    await db.commit()
    await db.refresh(server)

    # Clear cache
    await redis_manager.clear_server_cache(server_id)

    return server


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Delete server - admins can delete any server, users can only delete their own"""
    server = await get_server_with_permission(server_id, current_user, db)

    await db.delete(server)
    await db.commit()

    # Clear cache
    await redis_manager.clear_server_cache(server_id)

    return None
