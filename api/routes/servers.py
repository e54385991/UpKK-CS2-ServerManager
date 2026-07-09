"""
Server management routes
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from typing import List, Dict, Any
import asyncio
import asyncssh
import os
import re
import shutil
import shlex
import tempfile
import uuid

from modules import (
    Server, ServerCreate, ServerUpdate, ServerResponse, ServerResponseWithUser, AuthType,
    DeploymentLog, CustomCommand, CustomCommandCreate, CustomCommandUpdate,
    CustomCommandExecuteRequest, CustomCommandResponse, ActionResponse,
    S3BackupItem, S3RestoreRequest,
    CleanupScanResponse, CleanupDeleteRequest, CleanupDeleteResponse,
    get_db, User, UserResponse, get_current_active_user, get_current_admin_user, get_optional_current_user, generate_api_key,
    get_current_time, SystemSettings, ServerStatus
)
from modules.config import settings as app_settings
from services import redis_manager
from services.captcha_service import captcha_service
from services.game_cleanup_service import game_cleanup_service
from services.s3_backup_service import s3_backup_service
from services.ssh_manager import SSHManager

router = APIRouter(prefix="/servers", tags=["servers"])


async def get_server_with_permission(
    server_id: int,
    current_user: User,
    db: AsyncSession
) -> Server:
    """
    Get server by ID, checking user permissions.
    Admins can access any server, regular users can only access their own.
    """
    if current_user.is_admin:
        server = await Server.get_by_id(db, server_id)
    else:
        server = await Server.get_by_id_and_user(db, server_id, current_user.id)
    
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server not found"
        )
    
    return server


async def get_server_owner_user(db: AsyncSession, server: Server, current_user: User) -> User:
    """Get the server owner's user record, including when an admin is acting."""
    if current_user.id == server.user_id:
        return current_user

    owner = await db.get(User, server.user_id)
    if not owner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server owner not found"
        )
    return owner


async def get_custom_command_or_404(
    db: AsyncSession,
    server_id: int,
    command_id: int,
    current_user: User,
) -> CustomCommand:
    custom_command = await CustomCommand.get_by_id_server_and_user(
        db,
        command_id,
        server_id,
        current_user.id,
    )
    if not custom_command:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Custom command not found"
        )
    return custom_command


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
            lines.append(f"stdout:\n{stdout}")
        if stderr:
            lines.append(f"stderr:\n{stderr}")
        lines.append("")
    return "\n".join(lines).strip()


async def execute_custom_commands(
    server: Server,
    target: str,
    commands: str,
) -> Dict[str, Any]:
    command_lines = parse_custom_command_lines(commands)
    if not command_lines:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one command line is required"
        )

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
            screen_name = f"cs2server_{server.id}"
            check_cmd = f"screen -list | grep -F {shlex.quote(screen_name)} || true"
            _, stdout, _ = await ssh_manager.execute_command(check_cmd, timeout=10)
            if not stdout or screen_name not in stdout:
                return {
                    "success": False,
                    "message": "Game server is not running. Please start the server first.",
                    "target": target,
                    "results": [],
                }

            for index, command in enumerate(command_lines, start=1):
                stuff_cmd = f"screen -S {shlex.quote(screen_name)} -X stuff {shlex.quote(command + chr(10))}"
                success, stdout, stderr = await ssh_manager.execute_command(stuff_cmd, timeout=10)
                results.append({
                    "index": index,
                    "command": command,
                    "success": success,
                    "stdout": stdout,
                    "stderr": stderr,
                })
        elif target == "host":
            for index, command in enumerate(command_lines, start=1):
                success, stdout, stderr = await ssh_manager.execute_command(command, timeout=300)
                results.append({
                    "index": index,
                    "command": command,
                    "success": success,
                    "stdout": stdout,
                    "stderr": stderr,
                })
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid custom command target"
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


async def execute_and_log_custom_commands(
    db: AsyncSession,
    server: Server,
    target: str,
    commands: str,
    name: str = "One-time custom command",
) -> Dict[str, Any]:
    result = await execute_custom_commands(server, target, commands)
    output = format_custom_command_log(target, result.get("results", []))
    log = DeploymentLog(
        server_id=server.id,
        action=f"custom_command_{target}",
        status="success" if result["success"] else "failed",
        output=f"{name}\n\n{output}".strip(),
        error_message=None if result["success"] else result["message"],
    )
    db.add(log)
    await db.commit()
    return result


@router.post("", response_model=ServerResponse, status_code=status.HTTP_201_CREATED)
async def create_server(
    server_data: ServerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Create a new CS2 server"""
    # Validate CAPTCHA first
    is_valid = await captcha_service.validate_captcha(server_data.captcha_token, server_data.captcha_code)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired CAPTCHA code"
        )
    
    # Check if server name already exists for this user
    existing = await Server.get_by_name_and_user(db, server_data.name, current_user.id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Server with name '{server_data.name}' already exists"
        )
    
    # Check if server with same host and game_directory already exists for this user
    duplicate_server = await Server.get_by_host_directory_and_user(
        db, server_data.host, server_data.game_directory, current_user.id
    )
    if duplicate_server:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A server with the same host ({server_data.host}) and game directory ({server_data.game_directory}) already exists. "
                   f"If you want to add a new server on this host, please use a different game directory or manually delete the existing directory on the server first."
        )
    
    # Validate SSH connection before creating server (password authentication only)
    conn = None
    try:
        if not server_data.ssh_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SSH password is required"
            )
        
        # Step 1: Attempt SSH connection
        try:
            conn = await asyncssh.connect(
                server_data.host,
                port=server_data.ssh_port,
                username=server_data.ssh_user,
                password=server_data.ssh_password,
                known_hosts=None,
                connect_timeout=15
            )
        except asyncssh.PermissionDenied:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SSH authentication failed for {server_data.ssh_user}@{server_data.host}. Please verify your username and password."
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"SSH connection to {server_data.host}:{server_data.ssh_port} timed out. The server may be unreachable or too slow to respond. Please check the network connection and server status."
            )
        except asyncssh.ConnectionLost as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Connection to {server_data.host}:{server_data.ssh_port} was lost. Please check if the server is reachable."
            )
        except asyncssh.Error as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SSH connection to {server_data.host}:{server_data.ssh_port} failed: {str(e)}. Please verify the host and port."
            )
        
        # Step 2: Test command execution
        result = await conn.run("echo 'SSH connection successful'", check=False)
        
        if result.exit_status != 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SSH connection succeeded but command execution failed. Please verify that user {server_data.ssh_user} has proper shell access and permissions."
            )
        
        # Step 3: Create game directory with proper permissions
        # Use shlex.quote to safely escape the directory path
        game_dir_quoted = shlex.quote(server_data.game_directory)
        mkdir_cmd = f"mkdir -p {game_dir_quoted}"
        
        result = await conn.run(mkdir_cmd, check=False)
        if result.exit_status != 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to create game directory {server_data.game_directory}. Please check permissions and path."
            )
        
        # Set proper permissions (755 - owner can read/write/execute, others can read/execute)
        chmod_cmd = f"chmod 755 {game_dir_quoted}"
        result = await conn.run(chmod_cmd, check=False)
        if result.exit_status != 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to set permissions on game directory {server_data.game_directory}. Please check user permissions."
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to validate server connection: {str(e)}"
        )
    finally:
        # Ensure connection is always closed
        if conn:
            conn.close()
    
    # Create server with user_id, auto-generated API key, and password auth
    # Exclude captcha fields from server creation
    server_dict = server_data.model_dump(exclude={'captcha_token', 'captcha_code'})
    server_dict['auth_type'] = AuthType.PASSWORD  # Always use password authentication
    
    # Apply system default proxy settings if not explicitly set by user
    system_settings = await SystemSettings.get_settings(db)
    if system_settings:
        # If user hasn't explicitly set proxy mode, apply system defaults
        # Check if both proxy fields are in their default state (None/False)
        if not server_dict.get('use_panel_proxy') and not server_dict.get('github_proxy'):
            if system_settings.default_proxy_mode == 'panel':
                server_dict['use_panel_proxy'] = True
                server_dict['github_proxy'] = None
            elif system_settings.default_proxy_mode == 'github_url' and system_settings.github_proxy_url:
                server_dict['use_panel_proxy'] = False
                server_dict['github_proxy'] = system_settings.github_proxy_url
            # else: default_proxy_mode is 'direct', keep both as None/False
    
    server = Server(**server_dict, user_id=current_user.id, api_key=generate_api_key())
    db.add(server)
    await db.commit()
    await db.refresh(server)
    
    return server


@router.get("", response_model=List[ServerResponse])
async def list_servers(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List all servers owned by current user"""
    servers = await Server.get_all_by_user(db, current_user.id, skip, limit)
    return servers


@router.get("/admin/all", response_model=List[ServerResponseWithUser])
async def list_all_servers_admin(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
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
        server_dict['user'] = UserResponse.model_validate(user) if user else None
        result.append(ServerResponseWithUser(**server_dict))
    
    return result


@router.get("/disk-space-all")
async def get_all_servers_disk_space(
    force_refresh: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get cached disk space information for all servers owned by current user.
    
    Args:
        force_refresh: If True, bypass cache and read from system
    
    NOTE: This route MUST be defined before /{server_id} routes
    to avoid path parameter matching conflicts.
    """
    from services.system_info_helper import system_info_helper
    
    # Get all servers for current user
    servers = await Server.get_all_by_user(db, current_user.id)
    
    # Get disk space for all servers
    disk_space_map = await system_info_helper.get_all_servers_disk_space(servers, force_refresh=force_refresh)
    
    # Convert to string keys for JSON
    response = {str(k): v for k, v in disk_space_map.items()}
    
    return {
        "servers": response,
        "timestamp": get_current_time().isoformat()
    }


@router.get("/{server_id}", response_model=ServerResponse)
async def get_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get server by ID - admins can access any server, users can only access their own"""
    server = await get_server_with_permission(server_id, current_user, db)
    return server


@router.get("/{server_id}/cleanup/scan", response_model=CleanupScanResponse)
async def scan_server_cleanup(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Scan approved game directory cleanup candidates for this server"""
    server = await get_server_with_permission(server_id, current_user, db)
    ssh_manager = SSHManager()

    try:
        success, data, error = await game_cleanup_service.scan(ssh_manager, server)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error
            )
        return data
    finally:
        try:
            await ssh_manager.disconnect()
        except Exception:
            pass


@router.post("/{server_id}/cleanup/delete", response_model=CleanupDeleteResponse)
async def delete_server_cleanup_items(
    server_id: int,
    cleanup_data: CleanupDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Delete approved game directory cleanup candidates for this server"""
    server = await get_server_with_permission(server_id, current_user, db)
    ssh_manager = SSHManager()

    try:
        success, result, error = await game_cleanup_service.delete(
            ssh_manager,
            server,
            cleanup_data.mode,
            paths=cleanup_data.paths,
            confirmation_text=cleanup_data.confirmation_text,
        )
        if error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error
            )
        return result
    finally:
        try:
            await ssh_manager.disconnect()
        except Exception:
            pass


@router.get("/{server_id}/s3-backups", response_model=List[S3BackupItem])
async def list_server_s3_backups(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List S3 plugin backups for this server"""
    server = await get_server_with_permission(server_id, current_user, db)
    owner = await get_server_owner_user(db, server, current_user)

    success, backups, error = await s3_backup_service.list_backups(owner, server)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error
        )
    return backups


@router.post("/{server_id}/s3-restore")
async def restore_server_s3_backup(
    server_id: int,
    restore_data: S3RestoreRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Restore a selected S3 plugin backup to this server"""
    server = await get_server_with_permission(server_id, current_user, db)
    owner = await get_server_owner_user(db, server, current_user)

    if not s3_backup_service.validate_object_key(owner, server, restore_data.object_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Selected S3 backup does not belong to this server"
        )

    temp_dir = tempfile.mkdtemp(prefix="cs2_s3_restore_")
    local_path = os.path.join(
        temp_dir,
        s3_backup_service.safe_object_filename(restore_data.object_key)
    )
    ssh_manager = SSHManager()

    try:
        download_success, download_error = await s3_backup_service.download_backup(
            owner,
            server,
            restore_data.object_key,
            local_path,
        )
        if not download_success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=download_error
            )

        safety_success, safety_message = await ssh_manager.backup_plugins(server)
        if not safety_success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to create safety backup before restore: {safety_message}"
            )

        game_dir = server.game_directory.rstrip("/")
        filename = s3_backup_service.safe_object_filename(restore_data.object_key)
        remote_restore_path = f"{game_dir}/backups/s3-restore-{uuid.uuid4().hex[:8]}-{filename}"

        upload_success, upload_error = await ssh_manager.upload_file(local_path, remote_restore_path, server)
        if not upload_success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to upload restore archive to server: {upload_error}"
            )

        csgo_dir = f"{game_dir}/cs2/game/csgo"
        extract_success, extract_error = await ssh_manager.extract_archive(
            remote_restore_path,
            csgo_dir,
            server,
            overwrite=True,
        )
        if not extract_success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to extract restore archive: {extract_error}"
            )

        return {
            "success": True,
            "message": "S3 plugin backup restored successfully",
            "restored_from": restore_data.object_key,
            "remote_archive_path": remote_restore_path,
            "safety_backup": getattr(ssh_manager, "last_plugin_backup", None),
        }
    finally:
        try:
            await ssh_manager.disconnect()
        except Exception:
            pass
        shutil.rmtree(temp_dir, ignore_errors=True)


@router.get("/{server_id}/custom-commands", response_model=List[CustomCommandResponse])
async def list_custom_commands(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List saved quick commands for this server and current user"""
    server = await get_server_with_permission(server_id, current_user, db)
    return await CustomCommand.get_all_by_server_and_user(db, server.id, current_user.id)


@router.post("/{server_id}/custom-commands", response_model=CustomCommandResponse, status_code=status.HTTP_201_CREATED)
async def create_custom_command(
    server_id: int,
    command_data: CustomCommandCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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


@router.put("/{server_id}/custom-commands/{command_id}", response_model=CustomCommandResponse)
async def update_custom_command(
    server_id: int,
    command_id: int,
    command_data: CustomCommandUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Update a saved quick command"""
    server = await get_server_with_permission(server_id, current_user, db)
    custom_command = await get_custom_command_or_404(db, server.id, command_id, current_user)

    update_data = command_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(custom_command, key, value)

    db.add(custom_command)
    await db.commit()
    await db.refresh(custom_command)
    return custom_command


@router.delete("/{server_id}/custom-commands/{command_id}", response_model=ActionResponse)
async def delete_custom_command(
    server_id: int,
    command_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Delete a saved quick command"""
    server = await get_server_with_permission(server_id, current_user, db)
    custom_command = await get_custom_command_or_404(db, server.id, command_id, current_user)
    await db.delete(custom_command)
    await db.commit()
    return ActionResponse(success=True, message="Custom command deleted successfully")


@router.post("/{server_id}/custom-commands/execute", response_model=ActionResponse)
async def execute_one_time_custom_command(
    server_id: int,
    command_data: CustomCommandExecuteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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


@router.post("/{server_id}/custom-commands/{command_id}/execute", response_model=ActionResponse)
async def execute_saved_custom_command(
    server_id: int,
    command_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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


@router.put("/{server_id}", response_model=ServerResponse)
async def update_server(
    server_id: int,
    server_data: ServerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Update server - admins can update any server, users can only update their own"""
    server = await get_server_with_permission(server_id, current_user, db)
    
    # Fields that affect the startup command - changes require server restart to take effect
    startup_affecting_fields = {
        'game_port', 'game_directory', 'server_name', 'default_map',
        'max_players', 'game_mode', 'game_type', 'server_password',
        'rcon_password', 'steam_account_token', 'tv_enable', 'tv_port',
        'additional_parameters', 'ip_address', 'client_port', 'cpu_affinity'
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
    for key, value in update_data.items():
        setattr(server, key, value)
    
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


@router.post("/{server_id}/apply-system-defaults", response_model=ServerResponse)
async def apply_system_defaults_to_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Apply system default proxy settings to a server"""
    server = await get_server_with_permission(server_id, current_user, db)
    
    # Get system settings
    system_settings = await SystemSettings.get_settings(db)
    if not system_settings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="System settings not configured"
        )
    
    # Apply system default proxy mode
    if system_settings.default_proxy_mode == 'panel':
        server.use_panel_proxy = True
        server.github_proxy = None
    elif system_settings.default_proxy_mode == 'github_url':
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
    current_user: User = Depends(get_current_active_user)
):
    """Delete server - admins can delete any server, users can only delete their own"""
    server = await get_server_with_permission(server_id, current_user, db)
    
    await db.delete(server)
    await db.commit()
    
    # Clear cache
    await redis_manager.clear_server_cache(server_id)
    
    return None


@router.get("/{server_id}/monitoring-logs")
async def get_monitoring_logs(
    server_id: int,
    limit: int = 50,
    event_type: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get monitoring logs for a server from Redis"""
    from services.redis_manager import redis_manager
    import logging
    logger = logging.getLogger(__name__)
    
    # Verify server exists and user has access
    server = await get_server_with_permission(server_id, current_user, db)
    
    # Get logs from Redis
    try:
        logs = await redis_manager.get_monitoring_logs(
            server_id=server_id,
            event_type=event_type,
            limit=limit
        )
        logger.info(f"Retrieved {len(logs)} monitoring logs from Redis for server {server_id}")
        return logs
    except Exception as e:
        logger.error(f"Failed to get monitoring logs from Redis: {e}")
        return []


@router.get("/ping", dependencies=[])
async def ping():
    """
    Ultra-simple ping endpoint with zero imports and explicit empty dependencies.
    The dependencies=[] explicitly overrides any global router dependencies.
    If this returns 'Not authenticated', the issue is external (proxy, wrong server, etc.)
    """
    return {"status": "ok", "message": "pong"}


@router.get("/a2s-cache-test", dependencies=[])
async def test_a2s_cache():
    """
    Simple test endpoint to verify routing works.
    The dependencies=[] explicitly overrides any global router dependencies.
    If this returns 200 but /a2s-cache returns 422, 
    then the issue is with the a2s-cache endpoint itself.
    """
    return {
        "status": "ok",
        "message": "Test endpoint working - no dependencies, no validation",
        "timestamp": get_current_time().isoformat(),
        "note": "If you see this with 200 OK, routing is working correctly"
    }


@router.get("/a2s-cache", dependencies=[])
async def get_all_servers_a2s_cache():
    """
    Get cached A2S information for all servers.
    
    Completely rewritten endpoint with zero dependencies to prevent any 422 errors.
    No database, no authentication, no validation - just pure data retrieval.
    
    IMPORTANT: This route MUST be defined before /{server_id}/a2s-info
    to avoid path parameter matching conflicts.
    """
    import logging
    
    logger = logging.getLogger(__name__)
    logger.info("=== A2S-CACHE ENDPOINT CALLED ===")
    logger.info(f"Function signature has {len(get_all_servers_a2s_cache.__code__.co_varnames)} parameters")
    
    # Initialize response with current timestamp
    response = {
        "servers": {},
        "timestamp": get_current_time().isoformat(),
        "debug": {
            "endpoint": "a2s-cache",
            "version": "2.0-no-deps",
            "parameters": 0
        }
    }
    
    try:
        # Import services only when needed to avoid circular dependencies
        from services.a2s_cache_service import a2s_cache_service
        from modules.database import async_session_maker
        from modules.models import Server
        from sqlmodel import select
        
        logger.info("Starting database query...")
        # Use a separate session to avoid dependency injection issues
        async with async_session_maker() as session:
            # Get all servers from database
            result = await session.execute(select(Server))
            servers = result.scalars().all()
            logger.info(f"Found {len(servers)} servers in database")
            
            # Get cached data for each server
            for server in servers:
                try:
                    cached_info = await a2s_cache_service.get_cached_info(server.id)
                    if cached_info:
                        response["servers"][str(server.id)] = cached_info
                        logger.debug(f"Retrieved cache for server {server.id}")
                except Exception as e:
                    logger.error(f"Error getting cache for server {server.id}: {e}")
                    # Add minimal error info
                    response["servers"][str(server.id)] = {
                        "success": False,
                        "error": "Cache unavailable"
                    }
        
        logger.info(f"Successfully returning data for {len(response['servers'])} servers")
    except Exception as e:
        logger.error(f"Error in a2s-cache endpoint: {e}", exc_info=True)
        # Always return success with error details, never raise
        response["error"] = str(e)
    
    logger.info("=== A2S-CACHE ENDPOINT COMPLETE ===")
    # Always return a valid dict response
    return response


@router.get("/{server_id}/a2s-info")
async def get_server_a2s_info(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get A2S query information for a server"""
    from services.a2s_query import a2s_service
    
    # Verify server exists and user has access
    server = await get_server_with_permission(server_id, current_user, db)
    
    # Use configured A2S host/port or fall back to server host/game_port
    query_host = server.a2s_query_host or server.host
    query_port = server.a2s_query_port or server.game_port
    
    # Query server info
    info_success, server_info = await a2s_service.query_server_info(query_host, query_port)
    
    # Query players if server info was successful
    players_success = False
    player_list = None
    if info_success:
        players_success, player_list = await a2s_service.query_players(query_host, query_port)
    
    response = {
        "query_host": query_host,
        "query_port": query_port,
        "success": info_success,
        "server_info": server_info,
        "players": player_list if players_success else [],
        "timestamp": get_current_time().isoformat()
    }
    
    return response


@router.get("/{server_id}/cpu-count")
async def get_server_cpu_count(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get CPU core count from the remote server"""
    from services.ssh_manager import SSHManager
    
    # Verify server exists and user has access
    server = await get_server_with_permission(server_id, current_user, db)
    
    ssh_manager = SSHManager()
    
    try:
        # Connect to server
        success, msg = await ssh_manager.connect(server)
        if not success:
            return {
                "success": False,
                "cpu_count": 32,  # Default fallback
                "message": f"Failed to connect: {msg}"
            }
        
        # Get CPU count using nproc command
        success, stdout, stderr = await ssh_manager.execute_command("nproc")
        
        if success and stdout.strip().isdigit():
            cpu_count = int(stdout.strip())
            return {
                "success": True,
                "cpu_count": cpu_count,
                "message": "CPU count retrieved successfully"
            }
        else:
            # Fallback to /proc/cpuinfo
            success, stdout, stderr = await ssh_manager.execute_command("grep -c ^processor /proc/cpuinfo")
            if success and stdout.strip().isdigit():
                cpu_count = int(stdout.strip())
                return {
                    "success": True,
                    "cpu_count": cpu_count,
                    "message": "CPU count retrieved successfully"
                }
            else:
                return {
                    "success": False,
                    "cpu_count": 32,  # Default fallback
                    "message": "Failed to detect CPU count, using default"
                }
    except Exception as e:
        return {
            "success": False,
            "cpu_count": 32,  # Default fallback
            "message": f"Error: {str(e)}"
        }
    finally:
        await ssh_manager.disconnect()


@router.get("/{server_id}/disk-space")
async def get_server_disk_space(
    server_id: int,
    force_refresh: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get disk space information for server directory
    
    Args:
        force_refresh: If True, bypass cache and read from system
    """
    from services.system_info_helper import system_info_helper
    
    # Verify server exists and user has access
    server = await get_server_with_permission(server_id, current_user, db)
    
    # Get disk space info from system info helper
    disk_info = await system_info_helper.get_disk_space(server, force_refresh=force_refresh)
    
    if disk_info:
        return {
            "success": True,
            "disk_space": disk_info,
            "server_directory": server.game_directory
        }
    else:
        return {
            "success": False,
            "message": "Failed to retrieve disk space information",
            "server_directory": server.game_directory
        }


@router.get("/{server_id}/check-deployment")
async def check_server_deployment(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Check if server is actually deployed by verifying cs2 binary file exists
    
    Returns:
        {
            "is_deployed": bool,
            "binary_path": str,
            "message": str
        }
    """
    server = await Server.get_by_id_and_user(db, server_id, current_user.id)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server not found"
        )
    
    # Check if cs2 binary exists
    ssh_manager = SSHManager()
    
    binary_path = f"{server.game_directory}/cs2/game/bin/linuxsteamrt64/cs2"
    verify_cmd = f"test -f {binary_path} && echo 'exists' || echo 'missing'"
    
    try:
        success, msg = await ssh_manager.connect(server)
        if not success:
            return {
                "is_deployed": False,
                "binary_path": binary_path,
                "message": f"Could not connect to server: {msg}",
                "error": True
            }
        
        verify_success, verify_stdout, _ = await ssh_manager.execute_command(verify_cmd)
        await ssh_manager.disconnect()
        
        is_deployed = verify_success and 'exists' in verify_stdout
        
        return {
            "is_deployed": is_deployed,
            "binary_path": binary_path,
            "message": "Server is deployed" if is_deployed else "Server is not deployed",
            "error": False
        }
    except Exception as e:
        return {
            "is_deployed": False,
            "binary_path": binary_path,
            "message": f"Error checking deployment: {str(e)}",
            "error": True
        }


@router.post("/{server_id}/ssh-reconnect")
async def manual_ssh_reconnect(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Manually reconnect to a server and reset SSH health status
    
    This endpoint is used to restore a "completely_down" server after 
    manual intervention (e.g., fixing network issues, updating credentials).
    """
    # Get server and verify ownership
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    if server.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to access this server"
        )
    
    # Use SSH health monitor to perform manual reconnection
    from services.ssh_health_monitor import ssh_health_monitor
    
    success, message = await ssh_health_monitor.manual_reconnect(server_id)
    
    if success:
        return {
            "success": True,
            "message": message,
            "ssh_health_status": "healthy"
        }
    else:
        return {
            "success": False,
            "message": message,
            "ssh_health_status": server.ssh_health_status
        }


@router.get("/{server_id}/ssh-health")
async def get_ssh_health_status(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get SSH health status for a server"""
    # Get server and verify ownership
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    if server.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to access this server"
        )
    
    # Calculate offline duration estimate based on consecutive failures
    offline_duration_estimate = None
    if server.consecutive_ssh_failures > 0:
        check_interval_hours = server.ssh_health_check_interval_hours or 2
        offline_hours = server.consecutive_ssh_failures * check_interval_hours
        offline_duration_estimate = {
            "hours": offline_hours,
            "days": round(offline_hours / 24, 1),
            "description": f"~{offline_hours} hours ({round(offline_hours / 24, 1)} days)"
        }
    
    return {
        "server_id": server_id,
        "ssh_health_status": server.ssh_health_status,
        "consecutive_failures": server.consecutive_ssh_failures,
        "failure_threshold": server.ssh_health_failure_threshold or 84,
        "is_ssh_down": server.is_ssh_down,
        "last_ssh_success": server.last_ssh_success.isoformat() if server.last_ssh_success else None,
        "last_ssh_failure": server.last_ssh_failure.isoformat() if server.last_ssh_failure else None,
        "last_health_check": server.last_ssh_health_check.isoformat() if server.last_ssh_health_check else None,
        "check_interval_hours": server.ssh_health_check_interval_hours or 2,
        "offline_duration_estimate": offline_duration_estimate,
        "monitoring_enabled": server.enable_ssh_health_monitoring
    }


@router.get("/{server_id}/startup-command")
async def get_startup_command(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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

    if server.steam_account_token:
        params.append('+sv_setsteamaccount "***STEAM_TOKEN***"')

    if server.server_password:
        params.append('+sv_password "***PASSWORD***"')

    if server.rcon_password:
        params.append('+rcon_password "***RCON_PASSWORD***"')

    params.append(f"+game_mode {game_mode}")
    params.append(f"+game_type {game_type}")

    if server.tv_enable and server.tv_port:
        params.extend([
            "+tv_enable 1",
            f"+tv_port {server.tv_port}",
            '+tv_name "GOTV"',
        ])

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

    # CPU affinity prefix
    cpu_affinity_prefix = ""
    if server.cpu_affinity and re.match(r'^[\d,\-\s]+$', server.cpu_affinity.strip()):
        cpu_affinity_prefix = f"taskset -c {server.cpu_affinity.strip()} "

    # Build full command with screen wrapper (autorestart variant)
    autorestart_script_path = f"{server.game_directory}/cs2_autorestart.sh"
    api_key = server.api_key or ""
    backend_url = server.backend_url or app_settings.BACKEND_URL

    if api_key:
        start_cmd = (
            f"{cpu_affinity_prefix}screen -dmS cs2server_{server.id} "
            f"bash {autorestart_script_path} "
            f"{server.id} '***API_KEY***' '{backend_url}' '{server.game_directory}' "
            f"'{cs2_start_cmd}'"
        )
    else:
        start_cmd = (
            f"cd {game_bin_dir} && "
            f"export LD_LIBRARY_PATH=\"{game_bin_dir}:$LD_LIBRARY_PATH\" && "
            f"{cpu_affinity_prefix}screen -dmS cs2server_{server.id} "
            f"bash -c '{cs2_executable} {params_str} 2>&1 | tee {server.game_directory}/cs2/game/csgo/console.log'"
        )

    return {
        "startup_command": start_cmd,
        "cs2_command": f"{cs2_executable} {params_str}",
        "game_mode_resolved": f"{game_mode_str} (game_type: {game_type}, game_mode: {game_mode})"
    }
