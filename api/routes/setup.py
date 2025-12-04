"""
Server setup automation routes
"""
from fastapi import APIRouter, HTTPException, status, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from typing import Optional, Tuple, List
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio
import asyncssh
import secrets
import string
import time
import shlex
import uuid
from datetime import datetime

from services.captcha_service import captcha_service
from services.redis_manager import redis_manager
from modules import get_current_active_user, User, SSHServerSudo, get_db

router = APIRouter(prefix="/api/setup", tags=["setup"])


class SetupWebSocket:
    """WebSocket manager for setup progress updates"""
    
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
    
    async def connect(self, websocket: WebSocket, session_id: str):
        """Connect a WebSocket client"""
        await websocket.accept()
        self.active_connections[session_id] = websocket
    
    def disconnect(self, session_id: str):
        """Disconnect a WebSocket client"""
        if session_id in self.active_connections:
            del self.active_connections[session_id]
    
    async def send_message(self, session_id: str, message: dict):
        """Send message to connected client for a session"""
        if session_id in self.active_connections:
            try:
                await self.active_connections[session_id].send_json(message)
            except Exception:
                # Connection closed, remove it silently
                self.disconnect(session_id)


setup_ws = SetupWebSocket()


# Redis-based schemas
class RedisServerListItem(BaseModel):
    """Schema for Redis-stored server in list (without password)"""
    key: str = Field(..., description="Redis key for this server")
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    game_directory: str
    created_at: float = Field(..., description="Unix timestamp")


class RedisServerDetail(BaseModel):
    """Schema for Redis-stored server detail (with password)"""
    user_id: int
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    ssh_password: str
    game_directory: str
    created_at: float = Field(..., description="Unix timestamp")




class ServerSetupRequest(BaseModel):
    """Request model for automated server setup (password authentication only)"""
    name: str  # Friendly name for the server
    host: str
    ssh_port: int = 22
    ssh_user: str  # Can be root or regular user with sudo access
    ssh_password: str  # SSH password (required, key-based auth not supported)
    sudo_password: Optional[str] = None  # Required if ssh_user is not root and sudo needs password
    cs2_username: str = Field(default="cs2server", pattern=r"^[a-z_][a-z0-9_-]*$")  # User to create for CS2 (alphanumeric + _ - only)
    cs2_password: Optional[str] = None  # If None, will auto-generate
    auto_sudo: bool = True  # Automatically use sudo for non-root users
    captcha_token: str  # CAPTCHA token from /api/captcha/generate
    captcha_code: str  # User-entered CAPTCHA code
    save_config: bool = True  # Whether to save the initialized server config
    open_game_ports: bool = True  # Whether to open UDP ports 20000-40000 if UFW is enabled
    session_id: Optional[str] = None  # Optional session ID for WebSocket progress updates


class ServerSetupResponse(BaseModel):
    """Response model for setup operation"""
    success: bool
    message: str
    cs2_username: str
    cs2_password: str
    game_directory: str
    logs: list[str]
    initialized_server_id: Optional[str] = None  # Redis key of saved server if save_config is True
    session_id: Optional[str] = None  # Session ID for WebSocket progress updates (if requested)


def generate_secure_password(length: int = 16) -> str:
    """
    Generate a secure random password with special characters to meet PAM requirements
    Uses safe special characters and proper escaping to avoid shell issues
    """
    # Use safe special characters that are commonly accepted by PAM policies
    # Avoiding characters that have special meaning in shell: ' " ` $ \ ! and others
    safe_special_chars = "!@#%^&*()_+-=[]{}|;:,.<>?"
    
    # Build character sets
    lowercase = string.ascii_lowercase
    uppercase = string.ascii_uppercase
    digits = string.digits
    
    # Ensure password has at least one of each required type for PAM compliance
    password = [
        secrets.choice(lowercase),          # At least one lowercase
        secrets.choice(uppercase),          # At least one uppercase
        secrets.choice(digits),             # At least one digit
        secrets.choice(safe_special_chars), # At least one special character
    ]
    
    # Fill the rest randomly from all character sets
    all_chars = lowercase + uppercase + digits + safe_special_chars
    password += [secrets.choice(all_chars) for _ in range(length - 4)]
    
    # Shuffle to avoid predictable patterns
    secrets.SystemRandom().shuffle(password)
    return ''.join(password)


async def run_sudo_command(conn: asyncssh.SSHClientConnection, command: str, 
                          sudo_password: Optional[str] = None) -> Tuple[str, str, int]:
    """
    Run command with sudo, handling both passwordless and password-required sudo
    Returns: (stdout, stderr, exit_code)
    """
    if sudo_password:
        # Use echo to provide password to sudo
        full_command = f"echo '{sudo_password}' | sudo -S {command}"
    else:
        # Try passwordless sudo
        full_command = f"sudo {command}"
    
    result = await conn.run(full_command, check=False)
    return result.stdout, result.stderr, result.exit_status


async def send_setup_progress(session_id: Optional[str], log_message: str):
    """
    Helper to send setup progress via WebSocket if session_id is provided
    Silently fails if WebSocket connection is not available or fails
    """
    if session_id:
        try:
            await setup_ws.send_message(session_id, {
                "type": "log",
                "message": log_message,
                "timestamp": datetime.utcnow().isoformat()
            })
        except Exception:
            # WebSocket failures should not break the main setup flow
            # Silently ignore WebSocket errors
            pass


@router.websocket("/setup-progress/{session_id}")
async def setup_progress_websocket(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint for real-time setup progress updates
    
    Connect to this endpoint before starting auto-setup to receive real-time logs.
    The session_id should be passed to the /auto-setup endpoint.
    
    Messages format:
    {
        "type": "log",
        "message": "...",
        "timestamp": "2024-01-01T00:00:00"
    }
    """
    await setup_ws.connect(websocket, session_id)
    try:
        # Send initial connection message
        await websocket.send_json({
            "type": "info",
            "message": "WebSocket 连接已建立，等待设置开始...",
            "timestamp": datetime.utcnow().isoformat()
        })
        
        while True:
            # Keep connection alive and receive any client messages
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
    except Exception:
        pass
    finally:
        setup_ws.disconnect(session_id)


@router.post("/auto-setup", response_model=ServerSetupResponse)
async def auto_setup_server(
    setup_req: ServerSetupRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Automatically setup a server for CS2 deployment
    
    This endpoint:
    1. Connects to the target server (as root or regular user with sudo)
    2. Automatically detects if sudo is needed and available
    3. Installs required system dependencies
    4. Creates a dedicated CS2 user
    5. Sets up the game directory
    6. Returns credentials for CS2 Manager to use
    7. Optionally saves the initialized server configuration for reuse
    
    Supports:
    - Root user login (no sudo needed)
    - Regular user with passwordless sudo
    - Regular user with password sudo (requires sudo_password)
    
    **Authentication Required**: User must be logged in to use this endpoint.
    """
    # Validate CAPTCHA first
    is_valid = await captcha_service.validate_captcha(setup_req.captcha_token, setup_req.captcha_code)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired CAPTCHA code"
        )
    
    logs = []
    conn = None
    
    # Helper function to add log and send to WebSocket
    async def add_log(message: str):
        logs.append(message)
        await send_setup_progress(setup_req.session_id, message)
    
    try:
        # Generate CS2 user password if not provided
        cs2_password = setup_req.cs2_password or generate_secure_password()
        
        await add_log(f"正在连接到 {setup_req.host}:{setup_req.ssh_port} (用户: {setup_req.ssh_user})...")
        
        # Connect to server
        # Connect using password authentication only
        conn = await asyncssh.connect(
            host=setup_req.host,
            port=setup_req.ssh_port,
            username=setup_req.ssh_user,
            password=setup_req.ssh_password,
            known_hosts=None,
            connect_timeout=15
        )
        
        await add_log("✓ SSH 连接成功")
        
        # Detect if we need sudo
        result = await conn.run("whoami", check=False)
        ssh_current_user = result.stdout.strip()
        needs_sudo = ssh_current_user != "root"
        
        if needs_sudo:
            await add_log(f"检测到非 root 用户 ({ssh_current_user})，将使用 sudo")
            
            # If sudo_password not provided, try to use ssh_password
            sudo_pass = setup_req.sudo_password
            if not sudo_pass and setup_req.ssh_password:
                await add_log("尝试使用 SSH 密码作为 sudo 密码...")
                sudo_pass = setup_req.ssh_password
        else:
            await add_log("检测到 root 用户，无需 sudo")
            sudo_pass = None
        
        # Test sudo access
        if needs_sudo:
            await add_log("测试 sudo 权限...")
            stdout, stderr, exit_code = await run_sudo_command(
                conn, "echo 'sudo test successful'", sudo_pass
            )
            
            if exit_code != 0:
                # Try without password
                if sudo_pass:
                    await add_log("带密码的 sudo 失败，尝试无密码 sudo...")
                    stdout, stderr, exit_code = await run_sudo_command(conn, "echo 'sudo test'", None)
                    if exit_code == 0:
                        await add_log("✓ 无密码 sudo 可用")
                        sudo_pass = None
                    else:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"sudo 权限不足。请确保用户有 sudo 权限，或提供正确的 sudo 密码。错误: {stderr}"
                        )
                else:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="sudo 需要密码，请在 sudo_password 字段提供"
                    )
            else:
                await add_log("✓ sudo 权限验证成功")
        
        # Update package list
        await add_log("更新系统包列表...")
        if needs_sudo:
            stdout, stderr, exit_code = await run_sudo_command(
                conn, "apt-get update", sudo_pass
            )
        else:
            result = await conn.run("apt-get update", check=False)
            exit_code = result.exit_status
            stdout = result.stdout
            stderr = result.stderr
        
        # Show the actual output from apt-get update
        if stdout and stdout.strip():
            for line in stdout.strip().split('\n'):
                if line.strip():
                    await add_log(f"  {line}")
        
        if exit_code == 0:
            await add_log("✓ 包列表更新完成")
        else:
            await add_log(f"⚠ 包列表更新失败 (继续): {stderr[:100]}")
        
        # Install required packages
        await add_log("安装系统依赖 (lib32gcc-s1, lib32stdc++6, screen, curl, wget, p7zip-full, bzip2)...")
        packages = "lib32gcc-s1 lib32stdc++6 screen curl wget unzip p7zip-full bzip2"
        install_cmd = f"DEBIAN_FRONTEND=noninteractive apt-get install -y {packages}"
        
        if needs_sudo:
            stdout, stderr, exit_code = await run_sudo_command(conn, install_cmd, sudo_pass)
        else:
            result = await conn.run(install_cmd, check=False)
            exit_code = result.exit_status
            stdout = result.stdout
            stderr = result.stderr
        
        # Show the actual output from apt-get install
        if stdout and stdout.strip():
            for line in stdout.strip().split('\n'):
                if line.strip():
                    await add_log(f"  {line}")
        
        if exit_code == 0:
            await add_log("✓ 系统依赖安装完成")
        else:
            await add_log(f"⚠ 部分依赖安装可能失败: {stderr[:100]}")
        
        # Check if system is Ubuntu 24 and install libssl1.1 if needed
        await add_log("检测系统版本...")
        result = await conn.run("lsb_release -rs 2>/dev/null || cat /etc/os-release | grep VERSION_ID | cut -d'\"' -f2", check=False)
        os_version = result.stdout.strip()
        await add_log(f"系统版本: {os_version}")
        
        if os_version.startswith("24."):
            await add_log("检测到 Ubuntu 24，正在安装 libssl1.1...")
            try:
                # Upload libssl1.1 deb file via SFTP
                import os
                # Get the project root directory
                current_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                local_deb_path = os.path.join(current_dir, "static", "linux_lib", "ubuntu_24", "libssl1.1_1.1.1f-1ubuntu2.24_amd64.deb")
                remote_deb_path = "/tmp/libssl1.1_1.1.1f-1ubuntu2.24_amd64.deb"
                
                # Check if local file exists
                if not os.path.exists(local_deb_path):
                    await add_log(f"⚠ 本地文件不存在: {local_deb_path}")
                else:
                    await add_log(f"正在上传 libssl1.1 到远程服务器...")
                    
                    # Use SFTP to upload the file
                    async with conn.start_sftp_client() as sftp:
                        await sftp.put(local_deb_path, remote_deb_path)
                    
                    await add_log(f"✓ 文件上传完成: {remote_deb_path}")
                    
                    # Install the package
                    await add_log("正在安装 libssl1.1...")
                    install_libssl_cmd = f"dpkg -i {remote_deb_path}"
                    
                    if needs_sudo:
                        stdout, stderr, exit_code = await run_sudo_command(conn, install_libssl_cmd, sudo_pass)
                    else:
                        result = await conn.run(install_libssl_cmd, check=False)
                        exit_code = result.exit_status
                        stdout = result.stdout
                        stderr = result.stderr
                    
                    # Show the output
                    if stdout and stdout.strip():
                        for line in stdout.strip().split('\n'):
                            if line.strip():
                                await add_log(f"  {line}")
                    
                    if exit_code == 0:
                        await add_log("✓ libssl1.1 安装成功")
                    else:
                        await add_log(f"⚠ libssl1.1 安装可能失败: {stderr[:100]}")
                    
                    # Clean up the uploaded file
                    cleanup_cmd = f"rm -f {remote_deb_path}"
                    if needs_sudo:
                        await run_sudo_command(conn, cleanup_cmd, sudo_pass)
                    else:
                        await conn.run(cleanup_cmd, check=False)
                    await add_log("✓ 清理临时文件完成")
                    
            except Exception as e:
                await add_log(f"⚠ libssl1.1 安装过程出错: {str(e)}")
                # Don't fail the whole setup if libssl1.1 installation fails
        else:
            await add_log("非 Ubuntu 24 系统，跳过 libssl1.1 安装")
        
        # Check if user already exists
        await add_log(f"检查用户 {setup_req.cs2_username}...")
        result = await conn.run(f"id {setup_req.cs2_username}", check=False)
        
        user_exists = result.exit_status == 0
        if user_exists:
            await add_log(f"用户 {setup_req.cs2_username} 已存在，将更新密码")
        else:
            # Create CS2 user
            await add_log(f"创建用户 {setup_req.cs2_username}...")
            create_user_cmd = f"useradd -m -s /bin/bash {setup_req.cs2_username}"
            
            if needs_sudo:
                stdout, stderr, exit_code = await run_sudo_command(conn, create_user_cmd, sudo_pass)
            else:
                result = await conn.run(create_user_cmd, check=False)
                exit_code = result.exit_status
                stderr = result.stderr
            
            if exit_code == 0:
                await add_log(f"✓ 用户 {setup_req.cs2_username} 创建成功")
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"创建用户失败: {stderr}"
                )
        
        # Set user password
        # Use a here-document approach which is safe for special characters
        # and works properly with sudo
        await add_log("设置用户密码...")
        
        # Create a safe command using a here-document wrapped in bash -c
        # This avoids all shell escaping issues and works with sudo
        chpasswd_cmd = f"bash -c \"chpasswd <<'EOFPWD'\n{setup_req.cs2_username}:{cs2_password}\nEOFPWD\""
        
        if needs_sudo:
            stdout, stderr, exit_code = await run_sudo_command(conn, chpasswd_cmd, sudo_pass)
        else:
            result = await conn.run(chpasswd_cmd, check=False)
            exit_code = result.exit_status
            stderr = result.stderr
        
        if exit_code == 0:
            await add_log("✓ 密码设置成功")
        else:
            await add_log(f"⚠ 密码设置可能失败: {stderr[:100]}")
        
        # Create game directory
        game_dir = f"/home/{setup_req.cs2_username}/cs2"
        await add_log(f"创建游戏目录 {game_dir}...")
        mkdir_cmd = f"mkdir -p {game_dir}"
        
        if needs_sudo:
            stdout, stderr, exit_code = await run_sudo_command(conn, mkdir_cmd, sudo_pass)
        else:
            result = await conn.run(mkdir_cmd, check=False)
            exit_code = result.exit_status
        
        # Set ownership
        await add_log("设置目录权限...")
        chown_cmd = f"chown -R {setup_req.cs2_username}:{setup_req.cs2_username} /home/{setup_req.cs2_username}"
        
        if needs_sudo:
            stdout, stderr, exit_code = await run_sudo_command(conn, chown_cmd, sudo_pass)
        else:
            result = await conn.run(chown_cmd, check=False)
            exit_code = result.exit_status
        
        if exit_code == 0:
            await add_log("✓ 权限设置完成")
        
        # Configure UFW firewall if requested
        if setup_req.open_game_ports:
            await add_log("检查 UFW 防火墙状态...")
            
            # Check if UFW is installed and active
            ufw_check_cmd = "ufw status"
            if needs_sudo:
                stdout, stderr, exit_code = await run_sudo_command(conn, ufw_check_cmd, sudo_pass)
            else:
                result = await conn.run(ufw_check_cmd, check=False)
                stdout = result.stdout
                stderr = result.stderr
                exit_code = result.exit_status
            
            if exit_code == 0 and "Status: active" in stdout:
                await add_log("UFW 防火墙已启用，正在开放 UDP 20000~40000 端口...")
                
                # Open UDP ports 20000-40000 for game servers
                ufw_allow_cmd = "ufw allow 20000:40000/udp"
                if needs_sudo:
                    stdout, stderr, exit_code = await run_sudo_command(conn, ufw_allow_cmd, sudo_pass)
                else:
                    result = await conn.run(ufw_allow_cmd, check=False)
                    stdout = result.stdout
                    exit_code = result.exit_status
                    stderr = result.stderr
                
                # Show UFW command output
                if stdout and stdout.strip():
                    for line in stdout.strip().split('\n'):
                        if line.strip():
                            await add_log(f"  {line}")
                
                if exit_code == 0:
                    await add_log("✓ UDP 端口 20000~40000 已开放")
                else:
                    await add_log(f"⚠ 开放端口失败: {stderr[:100]}")
            elif exit_code != 0:
                await add_log("⚠ UFW 未安装或无法获取状态，跳过端口配置")
            else:
                await add_log("ℹ UFW 未启用，跳过端口配置")
        
        await add_log("=" * 50)
        await add_log("✓ 服务器环境设置完成！")
        await add_log("=" * 50)
        
        # Save sudo/SSH user information to database
        # Save for all users (root and non-root) to keep SSH credentials
        try:
            await add_log("正在保存 SSH 用户配置到数据库...")
            # Determine what password to save based on user type:
            # - Root user: save SSH password (used for SSH login)
            # - Sudo user with password: save sudo password
            # - Passwordless sudo: save empty string
            if not needs_sudo:
                # Root user - save SSH password for future SSH connections
                sudo_password_to_save = setup_req.ssh_password
                user_type = "root 用户"
            elif sudo_pass:
                # Sudo user with password - save sudo password
                sudo_password_to_save = sudo_pass
                user_type = "带密码 sudo"
            else:
                # Passwordless sudo - save empty string
                sudo_password_to_save = ""
                user_type = "无密码 sudo"
            
            await add_log(f"保存参数: user_id={current_user.id}, host={setup_req.host}, port={setup_req.ssh_port}, sudo_user={setup_req.ssh_user}, 类型={user_type}")
            await SSHServerSudo.upsert(
                session=db,
                user_id=current_user.id,
                host=setup_req.host,
                ssh_port=setup_req.ssh_port,
                sudo_user=setup_req.ssh_user,  # The SSH user we used for initialization (root or sudo user)
                sudo_password=sudo_password_to_save
            )
            await add_log(f"✓ SSH 用户配置已成功保存到数据库 (用户: {setup_req.ssh_user}, 类型: {user_type})")
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            await add_log(f"✗ 保存 SSH 用户配置失败: {str(e)}")
            await add_log(f"错误详情: {error_details}")
            # Don't fail the whole operation if saving config fails
        
        # Save initialized server configuration to Redis if requested (24-hour expiration)
        initialized_server_id = None
        if setup_req.save_config:
            try:
                await add_log("保存服务器配置到 Redis...")
                # Note: We save the CS2 user credentials (cs2server), not the SSH login credentials
                # This allows quick-fill when adding CS2 servers later
                server_data = {
                    'user_id': current_user.id,
                    'name': setup_req.name,
                    'host': setup_req.host,
                    'ssh_port': setup_req.ssh_port,
                    'ssh_user': setup_req.cs2_username,  # CS2 user (e.g., cs2server)
                    'ssh_password': cs2_password,  # CS2 user's password (auto-generated)
                    'game_directory': game_dir,
                    'created_at': time.time()
                }
                server_key = await redis_manager.set_initialized_server(current_user.id, server_data)
                initialized_server_id = server_key
                await add_log(f"✓ 服务器配置已保存到 Redis (用户: {setup_req.cs2_username}, 24小时有效期)")
            except Exception as e:
                import traceback
                error_details = traceback.format_exc()
                await add_log(f"⚠ 保存配置失败: {str(e)}")
                await add_log(f"错误详情: {error_details}")
                # Don't fail the whole operation if saving fails
        
        return ServerSetupResponse(
            success=True,
            message="服务器环境设置成功",
            cs2_username=setup_req.cs2_username,
            cs2_password=cs2_password,
            game_directory=game_dir,
            logs=logs,
            initialized_server_id=initialized_server_id,
            session_id=setup_req.session_id
        )
        
    except asyncssh.PermissionDenied:
        await add_log("✗ SSH 认证失败")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SSH 认证失败，请检查用户名和密码/密钥"
        )
    except asyncio.TimeoutError:
        await add_log("✗ SSH 连接超时")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="SSH 连接超时 - 服务器可能无法访问或响应过慢，请检查网络连接和服务器状态"
        )
    except asyncssh.Error as e:
        await add_log(f"✗ SSH 错误: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"SSH 连接错误: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        await add_log(f"✗ 未知错误: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"设置失败: {str(e)}"
        )
    finally:
        if conn:
            conn.close()
            await conn.wait_closed()


@router.get("/initialized-servers", response_model=List[RedisServerListItem])
async def list_initialized_servers(
    current_user: User = Depends(get_current_active_user)
):
    """
    List all initialized servers for the current user from Redis (without sensitive credentials)
    
    **Authentication Required**: User must be logged in.
    Note: Data stored in Redis with 24-hour expiration.
    """
    servers = await redis_manager.get_initialized_servers(current_user.id)
    
    # Remove sensitive data from list response
    safe_servers = []
    for server in servers:
        safe_server = RedisServerListItem(
            key=server.get('key'),
            name=server.get('name'),
            host=server.get('host'),
            ssh_port=server.get('ssh_port'),
            ssh_user=server.get('ssh_user'),
            game_directory=server.get('game_directory'),
            created_at=server.get('created_at')
        )
        safe_servers.append(safe_server)
    
    return safe_servers


@router.delete("/initialized-servers/{server_key:path}")
async def delete_initialized_server(
    server_key: str,
    current_user: User = Depends(get_current_active_user)
):
    """
    Delete an initialized server configuration from Redis
    
    **Authentication Required**: User must be logged in and own the server.
    """
    # Verify ownership by checking if server belongs to user
    server_data = await redis_manager.get_initialized_server(server_key)
    
    if not server_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Initialized server not found or already expired"
        )
    
    if server_data.get('user_id') != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this server configuration"
        )
    
    success = await redis_manager.delete_initialized_server(current_user.id, server_key)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete server configuration"
        )
    
    return {"success": True, "message": "Initialized server deleted successfully"}


@router.get("/initialized-servers/{server_key:path}", response_model=RedisServerDetail)
async def get_initialized_server(
    server_key: str,
    current_user: User = Depends(get_current_active_user)
):
    """
    Get a specific initialized server configuration from Redis (including credentials)
    
    **Authentication Required**: User must be logged in and own the server.
    """
    server_data = await redis_manager.get_initialized_server(server_key)
    
    if not server_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Initialized server not found or expired (24-hour limit)"
        )
    
    if server_data.get('user_id') != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this server configuration"
        )
    
    return RedisServerDetail(**server_data)

