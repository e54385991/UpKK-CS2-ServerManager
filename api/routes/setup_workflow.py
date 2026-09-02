"""Reusable setup workflow and contracts for the setup route."""

import asyncio
import secrets
import shlex
import string
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Tuple

import asyncssh
from fastapi import HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from modules import SSHServerSudo, User
from services.redis_manager import redis_manager
from services.ssh.text import decode_remote_text
from services.system_dependencies import (
    APT_RETRY_ATTEMPTS,
    APT_RETRY_DELAYS_SECONDS,
    SETUP_OPTIONAL_PACKAGES,
    SEVEN_ZIP_PACKAGE_ALTERNATIVES,
    STEAMCMD_REQUIRED_PACKAGES,
    apt_get_command,
    installed_packages_verification_command,
    normalize_debian_architecture,
    steamcmd_architecture_supported,
)


class ServerSetupRequest(BaseModel):
    """Request model for automated server setup (password authentication only)"""

    name: str  # Friendly name for the server
    host: str
    ssh_port: int = 22
    ssh_user: str  # Can be root or regular user with sudo access
    ssh_password: str  # SSH password (required, key-based auth not supported)
    sudo_password: Optional[str] = None  # Required if ssh_user is not root and sudo needs password
    cs2_username: str = Field(
        default="cs2server", pattern=r"^[a-z_][a-z0-9_-]*$"
    )  # User to create for CS2 (alphanumeric + _ - only)
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
        secrets.choice(lowercase),  # At least one lowercase
        secrets.choice(uppercase),  # At least one uppercase
        secrets.choice(digits),  # At least one digit
        secrets.choice(safe_special_chars),  # At least one special character
    ]

    # Fill the rest randomly from all character sets
    all_chars = lowercase + uppercase + digits + safe_special_chars
    password += [secrets.choice(all_chars) for _ in range(length - 4)]

    # Shuffle to avoid predictable patterns
    secrets.SystemRandom().shuffle(password)
    return "".join(password)


async def run_sudo_command(
    conn: asyncssh.SSHClientConnection,
    command: str,
    sudo_password: Optional[str] = None,
    timeout: Optional[float] = None,
) -> Tuple[str, str, int]:
    """
    Run command with sudo, handling both passwordless and password-required sudo
    Returns: (stdout, stderr, exit_code)
    """
    privileged_command = f"sudo -n -- sh -c {shlex.quote(command)}"
    if sudo_password:
        # Quote the password so credentials containing shell metacharacters stay data.
        privileged_command = (
            f"printf '%s\\n' {shlex.quote(sudo_password)} | sudo -S -- sh -c {shlex.quote(command)}"
        )

    result = await conn.run(privileged_command, check=False, timeout=timeout)
    return (
        decode_remote_text(result.stdout),
        decode_remote_text(result.stderr),
        int(result.exit_status or 0),
    )


async def run_admin_command(
    conn: asyncssh.SSHClientConnection,
    command: str,
    *,
    needs_sudo: bool,
    sudo_password: Optional[str],
    timeout: Optional[float] = None,
) -> Tuple[str, str, int]:
    """Run an internally generated command as root on the remote host."""
    if needs_sudo:
        return await run_sudo_command(conn, command, sudo_password, timeout=timeout)
    result = await conn.run(command, check=False, timeout=timeout)
    return (
        decode_remote_text(result.stdout),
        decode_remote_text(result.stderr),
        int(result.exit_status or 0),
    )


def _short_command_error(stdout: str, stderr: str, limit: int = 2000) -> str:
    combined = "\n".join(part.strip() for part in (stderr, stdout) if part and part.strip())
    if not combined:
        return "命令未返回错误详情"
    return combined[-limit:]


SetupLog = Callable[[str], Awaitable[None]]


@dataclass
class _SetupContext:
    request: ServerSetupRequest
    conn: asyncssh.SSHClientConnection
    add_log: SetupLog
    add_command_output: SetupLog
    cs2_password: str
    os_version: str = ""
    needs_sudo: bool = False
    sudo_password: Optional[str] = None
    game_directory: str = ""


async def _detect_setup_host(context: _SetupContext) -> None:
    """Validate the remote host and establish its privilege strategy."""
    architecture_result = await context.conn.run(
        "dpkg --print-architecture 2>/dev/null || uname -m", check=False
    )
    remote_architecture = normalize_debian_architecture(
        decode_remote_text(architecture_result.stdout)
    )
    await context.add_log(f"检测到系统架构: {remote_architecture or '未知'}")
    if architecture_result.exit_status != 0 or not steamcmd_architecture_supported(
        remote_architecture
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"不支持的服务器架构: {remote_architecture or '未知'}。"
                "SteamCMD/CS2 Linux 服务端需要 amd64 (x86_64)；"
                "arm64/aarch64 服务器不能原生运行。"
            ),
        )
    await context.add_log("✓ 架构兼容 SteamCMD/CS2")

    package_manager_result = await context.conn.run("command -v apt-get", check=False)
    if package_manager_result.exit_status != 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="目标服务器未安装 apt-get；自动设置仅支持 Ubuntu/Debian。",
        )

    await context.add_log("检测系统版本...")
    version_result = await context.conn.run(
        "lsb_release -rs 2>/dev/null || sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '\"'",
        check=False,
    )
    context.os_version = decode_remote_text(version_result.stdout).strip()
    await context.add_log(f"系统版本: {context.os_version or '未知'}")

    user_result = await context.conn.run("whoami", check=False)
    ssh_current_user = decode_remote_text(user_result.stdout).strip()
    context.needs_sudo = ssh_current_user != "root"
    if not context.needs_sudo:
        await context.add_log("检测到 root 用户，无需 sudo")
        return

    await context.add_log(f"检测到非 root 用户 ({ssh_current_user})，将使用 sudo")
    context.sudo_password = context.request.sudo_password or context.request.ssh_password
    if not context.request.sudo_password and context.request.ssh_password:
        await context.add_log("尝试使用 SSH 密码作为 sudo 密码...")

    await context.add_log("测试 sudo 权限...")
    _, stderr, exit_code = await run_sudo_command(
        context.conn, "echo 'sudo test successful'", context.sudo_password
    )
    if exit_code == 0:
        await context.add_log("✓ sudo 权限验证成功")
        return
    if context.sudo_password:
        await context.add_log("带密码的 sudo 失败，尝试无密码 sudo...")
        _, stderr, exit_code = await run_sudo_command(context.conn, "echo 'sudo test'", None)
        if exit_code == 0:
            await context.add_log("✓ 无密码 sudo 可用")
            context.sudo_password = None
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"sudo 权限不足。请确保用户有 sudo 权限，或提供正确的 sudo 密码。错误: {stderr}",
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="sudo 需要密码，请在 sudo_password 字段提供",
    )


async def _run_setup_command(context: _SetupContext, command: str) -> tuple[str, str, int]:
    return await run_admin_command(
        context.conn,
        command,
        needs_sudo=context.needs_sudo,
        sudo_password=context.sudo_password,
    )


async def _install_setup_dependencies(context: _SetupContext) -> None:
    """Install and verify the required runtime packages."""
    await context.add_log("更新系统包列表（自动等待 APT 锁并重试网络错误）...")
    stdout, stderr, exit_code = await run_apt_command_with_retry(
        context.conn,
        apt_get_command("update"),
        description="更新系统包列表",
        needs_sudo=context.needs_sudo,
        sudo_password=context.sudo_password,
        add_log=context.add_log,
    )
    if stdout.strip():
        await context.add_command_output(stdout)
    if exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"更新系统包列表失败，已自动重试: {_short_command_error(stdout, stderr)}",
        )
    await context.add_log("✓ 包列表更新完成")

    await context.add_log("检查 sudo 是否已安装...")
    sudo_check = await context.conn.run("command -v sudo", check=False)
    if sudo_check.exit_status != 0:
        await context.add_log("sudo 未安装，正在安装 sudo...")
        stdout, stderr, exit_code = await run_apt_command_with_retry(
            context.conn,
            apt_get_command("install", ("sudo",)),
            description="安装 sudo",
            needs_sudo=context.needs_sudo,
            sudo_password=context.sudo_password,
            add_log=context.add_log,
        )
        if exit_code != 0:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"安装 sudo 失败: {_short_command_error(stdout, stderr)}",
            )
        await context.add_log("✓ sudo 安装成功")
    else:
        await context.add_log("✓ sudo 已安装")

    await context.add_log(f"安装 SteamCMD 必需依赖: {', '.join(STEAMCMD_REQUIRED_PACKAGES)}")
    stdout, stderr, exit_code = await run_apt_command_with_retry(
        context.conn,
        apt_get_command("install", STEAMCMD_REQUIRED_PACKAGES),
        description="安装 SteamCMD 必需依赖",
        needs_sudo=context.needs_sudo,
        sudo_password=context.sudo_password,
        add_log=context.add_log,
    )
    if stdout.strip():
        await context.add_command_output(stdout)
    if exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"安装 SteamCMD 必需依赖失败: {_short_command_error(stdout, stderr)}",
        )
    verification = await context.conn.run(
        installed_packages_verification_command(STEAMCMD_REQUIRED_PACKAGES), check=False
    )
    if verification.exit_status != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "APT 返回成功，但依赖验证失败: "
                f"{_short_command_error(decode_remote_text(verification.stdout), decode_remote_text(verification.stderr))}"
            ),
        )
    await context.add_log("✓ SteamCMD 必需依赖已安装并验证")

    archive_package = None
    for candidate in SEVEN_ZIP_PACKAGE_ALTERNATIVES:
        result = await context.conn.run(
            f"apt-cache show --no-all-versions {shlex.quote(candidate)} >/dev/null 2>&1",
            check=False,
        )
        if result.exit_status == 0:
            archive_package = candidate
            break
    optional_packages: list[str] = list(SETUP_OPTIONAL_PACKAGES)
    if archive_package:
        optional_packages.append(archive_package)
    else:
        await context.add_log("⚠ 软件源中没有 7zip/p7zip-full；跳过可选的 7z 支持")
    if not optional_packages:
        return
    await context.add_log(f"安装可选增强依赖: {', '.join(optional_packages)}")
    stdout, stderr, exit_code = await run_apt_command_with_retry(
        context.conn,
        apt_get_command("install", optional_packages),
        description="安装可选增强依赖",
        needs_sudo=context.needs_sudo,
        sudo_password=context.sudo_password,
        add_log=context.add_log,
    )
    if exit_code == 0:
        await context.add_log("✓ 可选增强依赖安装完成")
    else:
        await context.add_log(
            "⚠ 可选增强依赖安装失败；SteamCMD 运行时已验证，将继续设置。"
            f"详情: {_short_command_error(stdout, stderr)}"
        )


async def _install_legacy_libssl(context: _SetupContext) -> None:
    if not context.os_version.startswith("24."):
        await context.add_log("非 Ubuntu 24 系统，跳过 libssl1.1 安装")
        return
    await context.add_log("检测到 Ubuntu 24，正在安装 libssl1.1...")
    try:
        import os

        current_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        local_path = os.path.join(
            current_dir,
            "static",
            "linux_lib",
            "ubuntu_24",
            "libssl1.1_1.1.1f-1ubuntu2.24_amd64.deb",
        )
        remote_path = "/tmp/libssl1.1_1.1.1f-1ubuntu2.24_amd64.deb"
        if not os.path.exists(local_path):
            await context.add_log(f"⚠ 本地文件不存在: {local_path}")
            return
        await context.add_log("正在上传 libssl1.1 到远程服务器...")
        async with context.conn.start_sftp_client() as sftp:
            await sftp.put(local_path, remote_path)
        await context.add_log(f"✓ 文件上传完成: {remote_path}")
        await context.add_log("正在安装 libssl1.1...")
        stdout, stderr, exit_code = await _run_setup_command(context, f"dpkg -i {remote_path}")
        if stdout.strip():
            await context.add_command_output(stdout)
        if exit_code == 0:
            await context.add_log("✓ libssl1.1 安装成功")
        else:
            await context.add_log(f"⚠ libssl1.1 安装可能失败: {stderr[:100]}")
        await _run_setup_command(context, f"rm -f {remote_path}")
        await context.add_log("✓ 清理临时文件完成")
    except Exception as exc:
        await context.add_log(f"⚠ libssl1.1 安装过程出错: {exc}")


async def _ensure_setup_user(context: _SetupContext) -> None:
    username = context.request.cs2_username
    await context.add_log(f"检查用户 {username}...")
    result = await context.conn.run(f"id {username}", check=False)
    if result.exit_status == 0:
        await context.add_log(f"用户 {username} 已存在，将更新密码")
    else:
        await context.add_log(f"创建用户 {username}...")
        stdout, stderr, exit_code = await _run_setup_command(
            context, f"useradd -m -s /bin/bash {username}"
        )
        if exit_code != 0:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"创建用户失败: {stderr}",
            )
        await context.add_log(f"✓ 用户 {username} 创建成功")

    await context.add_log("设置用户密码...")
    credential = f"{username}:{context.cs2_password}"
    stdout, stderr, exit_code = await _run_setup_command(
        context, f"printf '%s\\n' {shlex.quote(credential)} | chpasswd"
    )
    if exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"设置 CS2 用户密码失败: {_short_command_error(stdout, stderr)}",
        )
    await context.add_log("✓ 密码设置成功")


async def _configure_setup_directory(context: _SetupContext) -> None:
    username = context.request.cs2_username
    context.game_directory = f"/home/{username}/cs2"
    await context.add_log(f"将用户 {username} 添加到 sudo 组...")
    stdout, stderr, exit_code = await _run_setup_command(context, f"usermod -aG sudo {username}")
    if exit_code == 0:
        await context.add_log(f"✓ 用户 {username} 已添加到 sudo 组")
    else:
        await context.add_log(f"⚠ 添加用户到 sudo 组可能失败: {stderr[:100]}")

    await context.add_log(f"创建游戏目录 {context.game_directory}...")
    stdout, stderr, exit_code = await _run_setup_command(
        context, f"mkdir -p {context.game_directory}"
    )
    if exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建游戏目录失败: {_short_command_error(stdout, stderr)}",
        )
    await context.add_log("设置目录权限...")
    stdout, stderr, exit_code = await _run_setup_command(
        context, f"chown -R {username}:{username} /home/{username}"
    )
    if exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"设置目录权限失败: {_short_command_error(stdout, stderr)}",
        )
    await context.add_log("✓ 权限设置完成")


async def _configure_setup_firewall(context: _SetupContext) -> None:
    if not context.request.open_game_ports:
        return
    await context.add_log("检查 UFW 防火墙状态...")
    stdout, stderr, exit_code = await _run_setup_command(context, "ufw status")
    if exit_code == 0 and "Status: active" in stdout:
        await context.add_log("UFW 防火墙已启用，正在开放 UDP 20000~40000 端口...")
        stdout, stderr, exit_code = await _run_setup_command(context, "ufw allow 20000:40000/udp")
        if stdout.strip():
            await context.add_command_output(stdout)
        if exit_code == 0:
            await context.add_log("✓ UDP 端口 20000~40000 已开放")
        else:
            await context.add_log(f"⚠ 开放端口失败: {stderr[:100]}")
    elif exit_code != 0:
        await context.add_log("⚠ UFW 未安装或无法获取状态，跳过端口配置")
    else:
        await context.add_log("ℹ UFW 未启用，跳过端口配置")


async def _configure_setup_user(context: _SetupContext) -> None:
    await _ensure_setup_user(context)
    await _configure_setup_directory(context)
    await _configure_setup_firewall(context)


async def _persist_setup_configuration(
    context: _SetupContext,
    *,
    current_user: User,
    db: AsyncSession,
) -> str | None:
    """Persist reusable credentials while keeping persistence failures non-fatal."""
    initialized_server_id: str | None = None
    try:
        await context.add_log("正在保存 SSH 用户配置到数据库...")
        sudo_password_to_save = (
            context.request.ssh_password if not context.needs_sudo else context.sudo_password or ""
        )
        user_type = (
            "root 用户"
            if not context.needs_sudo
            else "带密码 sudo"
            if context.sudo_password
            else "无密码 sudo"
        )
        await SSHServerSudo.upsert(
            session=db,
            user_id=current_user.id,
            host=context.request.host,
            ssh_port=context.request.ssh_port,
            sudo_user=context.request.ssh_user,
            sudo_password=sudo_password_to_save,
        )
        await context.add_log(
            f"✓ SSH 用户配置已成功保存到数据库 (用户: {context.request.ssh_user}, 类型: {user_type})"
        )
    except Exception as exc:
        await context.add_log(f"✗ 保存 SSH 用户配置失败: {exc}")

    if not context.request.save_config:
        return initialized_server_id
    try:
        await context.add_log("保存服务器配置到 Redis...")
        server_data = {
            "user_id": current_user.id,
            "name": context.request.name,
            "host": context.request.host,
            "ssh_port": context.request.ssh_port,
            "ssh_user": context.request.cs2_username,
            "ssh_password": context.cs2_password,
            "game_directory": context.game_directory,
            "created_at": time.time(),
        }
        initialized_server_id = await redis_manager.set_initialized_server(
            current_user.id, server_data
        )
        await context.add_log(
            f"✓ 服务器配置已保存到 Redis (用户: {context.request.cs2_username}, 24小时有效期)"
        )
    except Exception as exc:
        await context.add_log(f"⚠ 保存配置失败: {exc}")
    return initialized_server_id


async def run_apt_command_with_retry(
    conn: asyncssh.SSHClientConnection,
    command: str,
    *,
    description: str,
    needs_sudo: bool,
    sudo_password: Optional[str],
    add_log: Callable[[str], Awaitable[None]],
    attempts: int = APT_RETRY_ATTEMPTS,
) -> Tuple[str, str, int]:
    """Run apt with bounded retries for transient network and dpkg-lock failures."""
    last_result = ("", "命令尚未执行", 1)
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            await add_log(f"重试 {description}（第 {attempt}/{attempts} 次）...")
        try:
            last_result = await run_admin_command(
                conn,
                command,
                needs_sudo=needs_sudo,
                sudo_password=sudo_password,
                timeout=600,
            )
        except asyncio.TimeoutError:
            last_result = ("", f"{description}在 600 秒后超时", 124)

        stdout, stderr, exit_code = last_result
        if exit_code == 0:
            return last_result

        await add_log(
            f"⚠ {description}失败（第 {attempt}/{attempts} 次）："
            f"{_short_command_error(stdout, stderr)}"
        )
        if attempt < attempts:
            delay = APT_RETRY_DELAYS_SECONDS[min(attempt - 1, len(APT_RETRY_DELAYS_SECONDS) - 1)]
            await add_log(f"将在 {delay} 秒后自动重试...")
            await asyncio.sleep(delay)

    return last_result
