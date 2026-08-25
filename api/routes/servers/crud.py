"""Servers crud endpoints."""

# ruff: noqa: F403,F405

import hashlib

from fastapi import Request

from api.dependencies import ActiveUser, AdminUser, DatabaseSession
from modules import ServerAgentPolicy
from services.audit_log_service import record_audit_event
from services.discord_binding_template_service import inherit_global_discord_binding

from .common import *

collection_router = APIRouter(prefix="/servers", tags=["servers"])
item_router = APIRouter(prefix="/servers", tags=["servers"])
mutation_router = APIRouter(prefix="/servers", tags=["servers"])


@collection_router.post("", response_model=ServerResponse, status_code=status.HTTP_201_CREATED)
async def create_server(
    server_data: ServerCreate,
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
):
    """Create a new CS2 server"""
    # Validate CAPTCHA first
    is_valid = await captcha_service.validate_captcha(
        server_data.captcha_token, server_data.captcha_code
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired CAPTCHA code"
        )

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
                known_hosts=None,
                connect_timeout=15,
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
    # Exclude captcha fields from server creation
    server_dict = server_data.model_dump(exclude={"captcha_token", "captcha_code"})
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

    server = Server(**server_dict, user_id=current_user.id, api_key=generate_api_key())
    db.add(server)
    await db.flush()
    db.add(ServerAgentPolicy(server_id=server.id))
    await inherit_global_discord_binding(db, server)
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
    await record_audit_event(
        category="server",
        action="server.create",
        status="success",
        user=current_user,
        request=request,
        server_id=server.id,
        details={"name": server.name, "host": server.host},
    )

    return server


@collection_router.get("", response_model=List[ServerResponse])
async def list_servers(
    skip: int = 0,
    limit: int = 100,
    db: DatabaseSession = None,
    current_user: ActiveUser = None,
):
    """List all servers owned by current user"""
    servers = await Server.get_all_by_user(db, current_user.id, skip, limit)
    return servers


@collection_router.get("/admin/all", response_model=List[ServerResponseWithUser])
async def list_all_servers_admin(
    skip: int = 0,
    limit: int = 100,
    db: DatabaseSession = None,
    current_user: AdminUser = None,
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


@item_router.get("/{server_id}", response_model=ServerResponse)
async def get_server(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """Get server by ID - admins can access any server, users can only access their own"""
    server = await get_server_with_permission(server_id, current_user, db)
    return server


@mutation_router.put("/{server_id}", response_model=ServerResponse)
async def update_server(
    server_id: int,
    server_data: ServerUpdate,
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
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
    server.sqlmodel_update(update_data)

    # Check if any startup-affecting fields changed while server is running
    restart_required = False
    if server.status == ServerStatus.RUNNING:
        for field in startup_affecting_fields:
            if field in update_data and old_values.get(field) != getattr(server, field):
                restart_required = True
                break

    await db.commit()
    await db.refresh(server)
    await record_audit_event(
        category="server",
        action="server.update",
        status="success",
        user=current_user,
        request=request,
        server_id=server_id,
        details={
            "changed_fields": [
                field
                for field in update_data
                if field
                not in {
                    "ssh_password",
                    "server_password",
                    "rcon_password",
                    "steam_account_token",
                }
            ]
        },
    )

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


@mutation_router.post("/{server_id}/apply-system-defaults", response_model=ServerResponse)
async def apply_system_defaults_to_server(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
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


@mutation_router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
):
    """Delete server - admins can delete any server, users can only delete their own"""
    server = await get_server_with_permission(server_id, current_user, db)
    server_name = server.name

    await db.delete(server)
    await db.commit()
    await record_audit_event(
        category="server",
        action="server.delete",
        status="success",
        user=current_user,
        request=request,
        server_id=server_id,
        details={"name": server_name},
    )

    # Clear cache
    await redis_manager.clear_server_cache(server_id)

    return None


router = APIRouter()
for _router in (collection_router, item_router, mutation_router):
    router.include_router(_router)
