"""Reusable setup workflow and contracts for the setup route."""

import asyncio
import contextlib
import os
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
from services.initialized_server_service import save_initialized_server
from services.redis_manager import redis_manager
from services.ssh.text import decode_remote_text
from services.system_dependencies import (
    APT_RETRY_ATTEMPTS,
    APT_RETRY_DELAYS_SECONDS,
    LEGACY_LIBSSL_DEB,
    LEGACY_LIBSSL_SHA256,
    LEGACY_LIBSSL_URL,
    SETUP_OPTIONAL_PACKAGES,
    SEVEN_ZIP_PACKAGE_ALTERNATIVES,
    STEAMCMD_REQUIRED_PACKAGES,
    apt_get_command,
    installed_packages_verification_command,
    legacy_libssl_present_command,
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
    captcha_token: Optional[str] = None  # CAPTCHA token from /api/captcha/generate
    captcha_code: Optional[str] = None  # User-entered CAPTCHA code
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
    initialized_server_id: Optional[str] = None  # Durable ID of saved server if save_config is True
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
        return "The command returned no error details"
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
    await context.add_log(f"Detected system architecture: {remote_architecture or 'unknown'}")
    if architecture_result.exit_status != 0 or not steamcmd_architecture_supported(
        remote_architecture
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported server architecture: {remote_architecture or 'unknown'}. "
                "SteamCMD/CS2 Linux servers require amd64 (x86_64); "
                "arm64/aarch64 servers cannot run natively."
            ),
        )
    await context.add_log("✓ Architecture is compatible with SteamCMD/CS2")

    package_manager_result = await context.conn.run("command -v apt-get", check=False)
    if package_manager_result.exit_status != 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The target server does not have apt-get installed; automatic setup supports Ubuntu/Debian only.",
        )

    await context.add_log("Checking system version...")
    version_result = await context.conn.run(
        "lsb_release -rs 2>/dev/null || sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '\"'",
        check=False,
    )
    context.os_version = decode_remote_text(version_result.stdout).strip()
    await context.add_log(f"System version: {context.os_version or 'unknown'}")

    user_result = await context.conn.run("whoami", check=False)
    ssh_current_user = decode_remote_text(user_result.stdout).strip()
    context.needs_sudo = ssh_current_user != "root"
    if not context.needs_sudo:
        await context.add_log("Detected root user; sudo is not required")
        return

    await context.add_log(f"Detected non-root user ({ssh_current_user}); sudo will be used")
    context.sudo_password = context.request.sudo_password or context.request.ssh_password
    if not context.request.sudo_password and context.request.ssh_password:
        await context.add_log("Trying the SSH password as the sudo password...")

    await context.add_log("Testing sudo access...")
    _, stderr, exit_code = await run_sudo_command(
        context.conn, "echo 'sudo test successful'", context.sudo_password
    )
    if exit_code == 0:
        await context.add_log("✓ Sudo access verified")
        return
    if context.sudo_password:
        await context.add_log("Sudo with a password failed; trying passwordless sudo...")
        _, stderr, exit_code = await run_sudo_command(context.conn, "echo 'sudo test'", None)
        if exit_code == 0:
            await context.add_log("✓ Passwordless sudo is available")
            context.sudo_password = None
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient sudo privileges. Ensure the user has sudo privileges or provide the correct sudo password. Error: {stderr}",
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="sudo requires a password; provide it in the sudo_password field",
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
    await context.add_log(
        "Updating package lists (waiting for APT locks and retrying network errors automatically)..."
    )
    stdout, stderr, exit_code = await run_apt_command_with_retry(
        context.conn,
        apt_get_command("update"),
        description="Updating package lists",
        needs_sudo=context.needs_sudo,
        sudo_password=context.sudo_password,
        add_log=context.add_log,
    )
    if stdout.strip():
        await context.add_command_output(stdout)
    if exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Package list update failed after automatic retries: {_short_command_error(stdout, stderr)}",
        )
    await context.add_log("✓ Package lists updated")

    await context.add_log("Checking whether sudo is installed...")
    sudo_check = await context.conn.run("command -v sudo", check=False)
    if sudo_check.exit_status != 0:
        await context.add_log("sudo is not installed; installing sudo...")
        stdout, stderr, exit_code = await run_apt_command_with_retry(
            context.conn,
            apt_get_command("install", ("sudo",)),
            description="Installing sudo",
            needs_sudo=context.needs_sudo,
            sudo_password=context.sudo_password,
            add_log=context.add_log,
        )
        if exit_code != 0:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to install sudo: {_short_command_error(stdout, stderr)}",
            )
        await context.add_log("✓ Sudo installed successfully")
    else:
        await context.add_log("✓ Sudo is already installed")

    await context.add_log(
        f"Installing required SteamCMD dependencies: {', '.join(STEAMCMD_REQUIRED_PACKAGES)}"
    )
    stdout, stderr, exit_code = await run_apt_command_with_retry(
        context.conn,
        apt_get_command("install", STEAMCMD_REQUIRED_PACKAGES),
        description="Installing required SteamCMD dependencies",
        needs_sudo=context.needs_sudo,
        sudo_password=context.sudo_password,
        add_log=context.add_log,
    )
    if stdout.strip():
        await context.add_command_output(stdout)
    if exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to install required SteamCMD dependencies: {_short_command_error(stdout, stderr)}",
        )
    verification = await context.conn.run(
        installed_packages_verification_command(STEAMCMD_REQUIRED_PACKAGES), check=False
    )
    if verification.exit_status != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "APT reported success, but dependency verification failed: "
                f"{_short_command_error(decode_remote_text(verification.stdout), decode_remote_text(verification.stderr))}"
            ),
        )
    await context.add_log("✓ Required SteamCMD dependencies installed and verified")

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
        await context.add_log(
            "⚠ No 7zip/p7zip-full package is available; skipping optional 7z support"
        )
    if not optional_packages:
        return
    await context.add_log(
        f"Installing optional enhancement dependencies: {', '.join(optional_packages)}"
    )
    stdout, stderr, exit_code = await run_apt_command_with_retry(
        context.conn,
        apt_get_command("install", optional_packages),
        description="Installing optional enhancement dependencies",
        needs_sudo=context.needs_sudo,
        sudo_password=context.sudo_password,
        add_log=context.add_log,
    )
    if exit_code == 0:
        await context.add_log("✓ Optional enhancement dependencies installed")
    else:
        await context.add_log(
            "⚠ Optional enhancement dependencies failed to install; "
            "the verified SteamCMD runtime will be used. "
            f"Details: {_short_command_error(stdout, stderr)}"
        )


def _bundled_legacy_libssl_path() -> str:
    """Absolute path of the OpenSSL 1.1 package shipped with the panel."""
    repository_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(repository_root, "static", "linux_lib", "ubuntu_24", LEGACY_LIBSSL_DEB)


async def _stage_legacy_libssl(context: _SetupContext, remote_path: str) -> bool:
    """Place the .deb on the host, preferring the copy bundled with the panel."""
    local_path = _bundled_legacy_libssl_path()
    if os.path.exists(local_path):
        await context.add_log("Uploading the bundled libssl1.1 package...")
        async with context.conn.start_sftp_client() as sftp:
            await sftp.put(local_path, remote_path)
        return True
    await context.add_log(
        f"Bundled package is missing ({local_path}); downloading {LEGACY_LIBSSL_URL}..."
    )
    _, stderr, exit_code = await _run_setup_command(
        context,
        f"curl -fsSL --retry 3 {shlex.quote(LEGACY_LIBSSL_URL)} -o {shlex.quote(remote_path)}",
    )
    if exit_code != 0:
        await context.add_log(f"⚠ Unable to download libssl1.1: {stderr[:200]}")
        return False
    return True


async def _install_legacy_libssl(context: _SetupContext) -> None:
    """Install the OpenSSL 1.1 runtime that older CS2 plugins still link against.

    Ubuntu 22.04+ and Debian 12+ dropped libssl1.1, and adding it needs root.
    Setup already holds root/sudo, so installing it here keeps later per-server
    plugin and game-mode flows free of privileged system changes. Only legacy
    plugins need it, so a failure is logged instead of failing setup.
    """
    probe = await context.conn.run(legacy_libssl_present_command(), check=False)
    if probe.exit_status == 0:
        await context.add_log("✓ OpenSSL 1.1 runtime is already present; skipping libssl1.1")
        return
    await context.add_log("Installing the libssl1.1 runtime required by legacy plugins...")
    remote_path = f"/tmp/{LEGACY_LIBSSL_DEB}"
    quoted_remote = shlex.quote(remote_path)
    try:
        if not await _stage_legacy_libssl(context, remote_path):
            return
        _, checksum_error, checksum_status = await _run_setup_command(
            context,
            f"echo {shlex.quote(f'{LEGACY_LIBSSL_SHA256}  {remote_path}')} | sha256sum -c -",
        )
        if checksum_status != 0:
            await context.add_log(
                f"⚠ libssl1.1 checksum verification failed: {checksum_error[:200]}"
            )
            return
        stdout, stderr, exit_code = await _run_setup_command(context, f"dpkg -i {quoted_remote}")
        if stdout.strip():
            await context.add_command_output(stdout)
        if exit_code == 0:
            await context.add_log("✓ libssl1.1 installed successfully")
        else:
            await context.add_log(f"⚠ libssl1.1 installation may have failed: {stderr[:100]}")
    except Exception as exc:
        await context.add_log(f"⚠ libssl1.1 installation error: {exc}")
    finally:
        # Cleanup must never replace the outcome already reported above.
        with contextlib.suppress(Exception):
            await _run_setup_command(context, f"rm -f {quoted_remote}")


async def _ensure_setup_user(context: _SetupContext) -> None:
    username = context.request.cs2_username
    await context.add_log(f"Checking user {username}...")
    result = await context.conn.run(f"id {username}", check=False)
    if result.exit_status == 0:
        await context.add_log(f"User {username} already exists; updating password")
    else:
        await context.add_log(f"Creating user {username}...")
        stdout, stderr, exit_code = await _run_setup_command(
            context, f"useradd -m -s /bin/bash {username}"
        )
        if exit_code != 0:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create user: {stderr}",
            )
        await context.add_log(f"✓ User {username} created successfully")

    await context.add_log("Setting the user password...")
    credential = f"{username}:{context.cs2_password}"
    stdout, stderr, exit_code = await _run_setup_command(
        context, f"printf '%s\\n' {shlex.quote(credential)} | chpasswd"
    )
    if exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to set the CS2 user password: {_short_command_error(stdout, stderr)}",
        )
    await context.add_log("✓ Password set successfully")


async def _configure_setup_directory(context: _SetupContext) -> None:
    username = context.request.cs2_username
    context.game_directory = f"/home/{username}/cs2"
    await context.add_log(f"Adding user {username} to the sudo group...")
    stdout, stderr, exit_code = await _run_setup_command(context, f"usermod -aG sudo {username}")
    if exit_code == 0:
        await context.add_log(f"✓ User {username} added to the sudo group")
    else:
        await context.add_log(
            f"⚠ Adding the user to the sudo group may have failed: {stderr[:100]}"
        )

    await context.add_log(f"Creating game directory {context.game_directory}...")
    stdout, stderr, exit_code = await _run_setup_command(
        context, f"mkdir -p {context.game_directory}"
    )
    if exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create the game directory: {_short_command_error(stdout, stderr)}",
        )
    await context.add_log("Setting directory permissions...")
    stdout, stderr, exit_code = await _run_setup_command(
        context, f"chown -R {username}:{username} /home/{username}"
    )
    if exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to set directory permissions: {_short_command_error(stdout, stderr)}",
        )
    await context.add_log("✓ Directory permissions set")


async def _configure_setup_firewall(context: _SetupContext) -> None:
    if not context.request.open_game_ports:
        return
    await context.add_log("Checking UFW firewall status...")
    stdout, stderr, exit_code = await _run_setup_command(context, "ufw status")
    if exit_code == 0 and "Status: active" in stdout:
        await context.add_log("UFW is enabled; opening UDP ports 20000-40000...")
        stdout, stderr, exit_code = await _run_setup_command(context, "ufw allow 20000:40000/udp")
        if stdout.strip():
            await context.add_command_output(stdout)
        if exit_code == 0:
            await context.add_log("✓ UDP ports 20000-40000 opened")
        else:
            await context.add_log(f"⚠ Failed to open ports: {stderr[:100]}")
    elif exit_code != 0:
        await context.add_log(
            "⚠ UFW is not installed or its status is unavailable; skipping port configuration"
        )
    else:
        await context.add_log("ℹ UFW is not enabled; skipping port configuration")


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
        await context.add_log("Saving SSH user configuration to the database...")
        sudo_password_to_save = (
            context.request.ssh_password if not context.needs_sudo else context.sudo_password or ""
        )
        user_type = (
            "root user"
            if not context.needs_sudo
            else "sudo with password"
            if context.sudo_password
            else "passwordless sudo"
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
            f"✓ SSH user configuration saved to the database (user: {context.request.ssh_user}, type: {user_type})"
        )
    except Exception as exc:
        await context.add_log(f"✗ Failed to save SSH user configuration: {exc}")

    if not context.request.save_config:
        return initialized_server_id
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
    try:
        await context.add_log("Saving server configuration to the database...")
        initialized_server_id = await save_initialized_server(
            db,
            user_id=current_user.id,
            name=context.request.name,
            host=context.request.host,
            ssh_port=context.request.ssh_port,
            ssh_user=context.request.cs2_username,
            ssh_password=context.cs2_password,
            game_directory=context.game_directory,
        )
        await context.add_log(
            f"✓ Server configuration saved permanently (user: {context.request.cs2_username})"
        )
    except Exception as exc:
        await context.add_log(f"⚠ Database save failed; trying Redis compatibility storage: {exc}")
        try:
            initialized_server_id = await redis_manager.set_initialized_server(
                current_user.id, server_data
            )
            await context.add_log(
                f"⚠ Saved to Redis compatibility storage (user: {context.request.cs2_username}; legacy data is retained for up to 30 days)"
            )
        except Exception as fallback_exc:
            await context.add_log(f"⚠ Failed to save configuration: {fallback_exc}")
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
    last_result = ("", "Command has not been executed", 1)
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            await add_log(f"Retrying {description} (attempt {attempt}/{attempts})...")
        try:
            last_result = await run_admin_command(
                conn,
                command,
                needs_sudo=needs_sudo,
                sudo_password=sudo_password,
                timeout=600,
            )
        except asyncio.TimeoutError:
            last_result = ("", f"{description} timed out after 600 seconds", 124)

        stdout, stderr, exit_code = last_result
        if exit_code == 0:
            return last_result

        await add_log(
            f"⚠ {description} failed (attempt {attempt}/{attempts}): "
            f"{_short_command_error(stdout, stderr)}"
        )
        if attempt < attempts:
            delay = APT_RETRY_DELAYS_SECONDS[min(attempt - 1, len(APT_RETRY_DELAYS_SECONDS) - 1)]
            await add_log(f"Retrying automatically in {delay} seconds...")
            await asyncio.sleep(delay)

    return last_result
