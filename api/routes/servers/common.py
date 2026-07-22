# ruff: noqa: F401
"""
Server management routes
"""

import asyncio
import os
import re
import shlex
import shutil
import tempfile
import uuid
from typing import Any, Dict, List

import asyncssh
from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.dependencies import require_server_access
from modules import (
    DEFAULT_PLUGIN_CONFIG_SOURCE_PATHS,
    ActionResponse,
    AuthType,
    CleanupDeleteRequest,
    CleanupDeleteResponse,
    CleanupScanResponse,
    CustomCommand,
    CustomCommandCreate,
    CustomCommandExecuteRequest,
    CustomCommandResponse,
    CustomCommandUpdate,
    DeploymentLog,
    DiscordSettingsResponse,
    DiscordSettingsUpdate,
    DiscordTestRequest,
    PluginConfigSource,
    S3BackupItem,
    S3RestoreRequest,
    Server,
    ServerCreate,
    ServerResponse,
    ServerResponseWithUser,
    ServerStatus,
    ServerUpdate,
    SystemSettings,
    User,
    UserResponse,
    generate_api_key,
    get_current_active_user,
    get_current_admin_user,
    get_current_time,
    get_db,
)
from modules.config import settings as app_settings
from services import redis_manager
from services.captcha_service import captcha_service
from services.discord_notification_service import discord_notification_service
from services.game_cleanup_service import game_cleanup_service
from services.game_session import (
    find_running_session_manager,
    gslt_startup_parameter,
    normalize_session_manager,
    send_keys_command,
    session_name,
    start_session_command,
)
from services.s3_backup_service import s3_backup_service
from services.ssh_manager import SSHManager


async def get_server_with_permission(
    server_id: int, current_user: User, db: AsyncSession
) -> Server:
    """
    Get server by ID, checking user permissions.
    Admins can access any server, regular users can only access their own.
    """
    return await require_server_access(db, server_id, current_user)


async def get_server_owner_user(db: AsyncSession, server: Server, current_user: User) -> User:
    """Get the server owner's user record, including when an admin is acting."""
    if current_user.id == server.user_id:
        return current_user

    owner = await db.get(User, server.user_id)
    if not owner:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server owner not found")
    await db.commit()
    return owner


def build_discord_settings_response(server: Server) -> DiscordSettingsResponse:
    """Build a Discord settings response without exposing the webhook URL."""
    return DiscordSettingsResponse(
        discord_notifications_enabled=server.discord_notifications_enabled,
        discord_channel_name=server.discord_channel_name,
        webhook_configured=discord_notification_service.webhook_configured(server),
        discord_notify_auto_updates=server.discord_notify_auto_updates,
        discord_notify_manual_updates=server.discord_notify_manual_updates,
        discord_notify_plugin_updates=server.discord_notify_plugin_updates,
        discord_notify_s3_backups=server.discord_notify_s3_backups,
        discord_notify_crash_restarts=server.discord_notify_crash_restarts,
        discord_crash_restart_min_interval_minutes=server.discord_crash_restart_min_interval_minutes
        or 10,
    )


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
            status_code=status.HTTP_404_NOT_FOUND, detail="Custom command not found"
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
            status_code=status.HTTP_400_BAD_REQUEST, detail="At least one command line is required"
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
            name = session_name(server.id)
            active_manager = await find_running_session_manager(
                ssh_manager.execute_command,
                server.session_manager,
                name,
            )
            if not active_manager:
                return {
                    "success": False,
                    "message": "Game server is not running. Please start the server first.",
                    "target": target,
                    "results": [],
                }

            for index, command in enumerate(command_lines, start=1):
                input_cmd = send_keys_command(active_manager, name, command)
                success, stdout, stderr = await ssh_manager.execute_command(input_cmd, timeout=10)
                results.append(
                    {
                        "index": index,
                        "command": command,
                        "success": success,
                        "stdout": stdout,
                        "stderr": stderr,
                    }
                )
        elif target == "host":
            for index, command in enumerate(command_lines, start=1):
                success, stdout, stderr = await ssh_manager.execute_command(command, timeout=300)
                results.append(
                    {
                        "index": index,
                        "command": command,
                        "success": success,
                        "stdout": stdout,
                        "stderr": stderr,
                    }
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid custom command target"
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


# Export private helpers too: endpoint modules are mechanical domain slices.
__all__ = [name for name in globals() if not name.startswith("__")]
