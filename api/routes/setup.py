"""
Server setup automation routes
"""
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import Optional, Tuple
import asyncssh
import secrets
import string

from services.captcha_service import captcha_service

router = APIRouter(prefix="/api/setup", tags=["setup"])


class ServerSetupRequest(BaseModel):
    """Request model for automated server setup"""
    host: str
    ssh_port: int = 22
    ssh_user: str  # Can be root or regular user with sudo access
    ssh_password: Optional[str] = None
    ssh_key_path: Optional[str] = None
    sudo_password: Optional[str] = None  # Required if ssh_user is not root and sudo needs password
    cs2_username: str = "cs2server"  # User to create for CS2
    cs2_password: Optional[str] = None  # If None, will auto-generate
    auto_sudo: bool = True  # Automatically use sudo for non-root users
    captcha_token: str  # CAPTCHA token from /api/captcha/generate
    captcha_code: str  # User-entered CAPTCHA code


class ServerSetupResponse(BaseModel):
    """Response model for setup operation"""
    success: bool
    message: str
    cs2_username: str
    cs2_password: str
    game_directory: str
    logs: list[str]


def generate_secure_password(length: int = 16) -> str:
    """Generate a secure random password"""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    # Ensure password has at least one of each type
    password = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*"),
    ]
    # Fill the rest randomly
    password += [secrets.choice(alphabet) for _ in range(length - 4)]
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


@router.post("/auto-setup", response_model=ServerSetupResponse)
async def auto_setup_server(setup_req: ServerSetupRequest):
    """
    Automatically setup a server for CS2 deployment
    
    This endpoint:
    1. Connects to the target server (as root or regular user with sudo)
    2. Automatically detects if sudo is needed and available
    3. Installs required system dependencies
    4. Creates a dedicated CS2 user
    5. Sets up the game directory
    6. Returns credentials for CS2 Manager to use
    
    Supports:
    - Root user login (no sudo needed)
    - Regular user with passwordless sudo
    - Regular user with password sudo (requires sudo_password)
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
    
    try:
        # Generate CS2 user password if not provided
        cs2_password = setup_req.cs2_password or generate_secure_password()
        
        logs.append(f"正在连接到 {setup_req.host}:{setup_req.ssh_port} (用户: {setup_req.ssh_user})...")
        
        # Connect to server
        if setup_req.ssh_password:
            conn = await asyncssh.connect(
                host=setup_req.host,
                port=setup_req.ssh_port,
                username=setup_req.ssh_user,
                password=setup_req.ssh_password,
                known_hosts=None
            )
        elif setup_req.ssh_key_path:
            conn = await asyncssh.connect(
                host=setup_req.host,
                port=setup_req.ssh_port,
                username=setup_req.ssh_user,
                client_keys=[setup_req.ssh_key_path],
                known_hosts=None
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="必须提供 SSH 密码或密钥文件"
            )
        
        logs.append("✓ SSH 连接成功")
        
        # Detect if we need sudo
        result = await conn.run("whoami", check=False)
        current_user = result.stdout.strip()
        needs_sudo = current_user != "root"
        
        if needs_sudo:
            logs.append(f"检测到非 root 用户 ({current_user})，将使用 sudo")
            
            # If sudo_password not provided, try to use ssh_password
            sudo_pass = setup_req.sudo_password
            if not sudo_pass and setup_req.ssh_password:
                logs.append("尝试使用 SSH 密码作为 sudo 密码...")
                sudo_pass = setup_req.ssh_password
        else:
            logs.append("检测到 root 用户，无需 sudo")
            sudo_pass = None
        
        # Test sudo access
        if needs_sudo:
            logs.append("测试 sudo 权限...")
            stdout, stderr, exit_code = await run_sudo_command(
                conn, "echo 'sudo test successful'", sudo_pass
            )
            
            if exit_code != 0:
                # Try without password
                if sudo_pass:
                    logs.append("带密码的 sudo 失败，尝试无密码 sudo...")
                    stdout, stderr, exit_code = await run_sudo_command(conn, "echo 'sudo test'", None)
                    if exit_code == 0:
                        logs.append("✓ 无密码 sudo 可用")
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
                logs.append("✓ sudo 权限验证成功")
        
        # Update package list
        logs.append("更新系统包列表...")
        if needs_sudo:
            stdout, stderr, exit_code = await run_sudo_command(
                conn, "apt-get update -qq", sudo_pass
            )
        else:
            result = await conn.run("apt-get update -qq", check=False)
            exit_code = result.exit_status
            stderr = result.stderr
        
        if exit_code == 0:
            logs.append("✓ 包列表更新完成")
        else:
            logs.append(f"⚠ 包列表更新失败 (继续): {stderr[:100]}")
        
        # Install required packages
        logs.append("安装系统依赖 (lib32gcc-s1, lib32stdc++6, screen, curl, wget)...")
        packages = "lib32gcc-s1 lib32stdc++6 screen curl wget unzip"
        install_cmd = f"DEBIAN_FRONTEND=noninteractive apt-get install -y {packages}"
        
        if needs_sudo:
            stdout, stderr, exit_code = await run_sudo_command(conn, install_cmd, sudo_pass)
        else:
            result = await conn.run(install_cmd, check=False)
            exit_code = result.exit_status
            stderr = result.stderr
        
        if exit_code == 0:
            logs.append("✓ 系统依赖安装完成")
        else:
            logs.append(f"⚠ 部分依赖安装可能失败: {stderr[:100]}")
        
        # Check if user already exists
        logs.append(f"检查用户 {setup_req.cs2_username}...")
        result = await conn.run(f"id {setup_req.cs2_username}", check=False)
        
        user_exists = result.exit_status == 0
        if user_exists:
            logs.append(f"用户 {setup_req.cs2_username} 已存在，将更新密码")
        else:
            # Create CS2 user
            logs.append(f"创建用户 {setup_req.cs2_username}...")
            create_user_cmd = f"useradd -m -s /bin/bash {setup_req.cs2_username}"
            
            if needs_sudo:
                stdout, stderr, exit_code = await run_sudo_command(conn, create_user_cmd, sudo_pass)
            else:
                result = await conn.run(create_user_cmd, check=False)
                exit_code = result.exit_status
                stderr = result.stderr
            
            if exit_code == 0:
                logs.append(f"✓ 用户 {setup_req.cs2_username} 创建成功")
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"创建用户失败: {stderr}"
                )
        
        # Set user password
        logs.append("设置用户密码...")
        chpasswd_cmd = f"sh -c \"echo '{setup_req.cs2_username}:{cs2_password}' | chpasswd\""
        
        if needs_sudo:
            stdout, stderr, exit_code = await run_sudo_command(conn, chpasswd_cmd, sudo_pass)
        else:
            result = await conn.run(chpasswd_cmd, check=False)
            exit_code = result.exit_status
            stderr = result.stderr
        
        if exit_code == 0:
            logs.append("✓ 密码设置成功")
        else:
            logs.append(f"⚠ 密码设置可能失败: {stderr[:100]}")
        
        # Create game directory
        game_dir = f"/home/{setup_req.cs2_username}/cs2"
        logs.append(f"创建游戏目录 {game_dir}...")
        mkdir_cmd = f"mkdir -p {game_dir}"
        
        if needs_sudo:
            stdout, stderr, exit_code = await run_sudo_command(conn, mkdir_cmd, sudo_pass)
        else:
            result = await conn.run(mkdir_cmd, check=False)
            exit_code = result.exit_status
        
        # Set ownership
        logs.append("设置目录权限...")
        chown_cmd = f"chown -R {setup_req.cs2_username}:{setup_req.cs2_username} /home/{setup_req.cs2_username}"
        
        if needs_sudo:
            stdout, stderr, exit_code = await run_sudo_command(conn, chown_cmd, sudo_pass)
        else:
            result = await conn.run(chown_cmd, check=False)
            exit_code = result.exit_status
        
        if exit_code == 0:
            logs.append("✓ 权限设置完成")
        
        logs.append("=" * 50)
        logs.append("✓ 服务器环境设置完成！")
        logs.append("=" * 50)
        
        return ServerSetupResponse(
            success=True,
            message="服务器环境设置成功",
            cs2_username=setup_req.cs2_username,
            cs2_password=cs2_password,
            game_directory=game_dir,
            logs=logs
        )
        
    except asyncssh.PermissionDenied:
        logs.append("✗ SSH 认证失败")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="SSH 认证失败，请检查用户名和密码/密钥"
        )
    except asyncssh.Error as e:
        logs.append(f"✗ SSH 错误: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"SSH 连接错误: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logs.append(f"✗ 未知错误: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"设置失败: {str(e)}"
        )
    finally:
        if conn:
            conn.close()
            await conn.wait_closed()
