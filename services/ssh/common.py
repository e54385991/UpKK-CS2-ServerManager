"""Shared imports and process-level helpers for SSH operations."""

# ruff: noqa: F401

import asyncio
import contextlib
import inspect
import ipaddress
import logging
import os
import posixpath
import re
import shlex
import shutil
import socket
import tempfile
import time
import uuid
from datetime import datetime
from email.message import Message
from email.utils import collapse_rfc2231_value
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urljoin, urlsplit

import anyio
import asyncssh

from modules.models import Server
from services.game_session import (
    availability_command,
    capture_console_command,
    cleanup_command,
    find_running_session_manager,
    find_running_session_managers,
    force_stop_session_command,
    gslt_startup_parameter,
    normalize_session_manager,
    session_manager_order,
    session_name,
    start_session_command,
    steamcmd_session_name,
    stop_session_command,
)
from services.server_monitor import server_monitor
from services.ssh_connection_pool import ssh_connection_pool

logger = logging.getLogger(__name__)


class SSHMixinBase:
    """Typed boundary for capabilities composed onto the SSH facade."""

    REMOTE_DOWNLOAD_TIMEOUT = 1800
    REMOTE_DOWNLOAD_MAX_BYTES = 20 * 1024 * 1024 * 1024
    REMOTE_DOWNLOAD_METADATA_MAX_BYTES = 256 * 1024
    REMOTE_DOWNLOAD_FILENAME_MAX_BYTES = 255
    REMOTE_DOWNLOAD_URL_MAX_LENGTH = 4096
    REMOTE_DOWNLOAD_MAX_REDIRECTS = 10
    REMOTE_DOWNLOAD_REDIRECT_CODES = frozenset((301, 302, 303, 307, 308))
    ARCHIVE_LISTING_STOP_TIMEOUT = 5
    ARCHIVE_MAX_MEMBER_PATH_BYTES = 4096
    ARCHIVE_MAX_ENTRIES = 10000
    ARCHIVE_MAX_FOLDERS = 2000
    CS2_EXECUTABLE_RELATIVE_PATH = "game/bin/linuxsteamrt64/cs2"

    def __getattr__(self, name: str) -> Any:
        """Expose dynamically composed sibling capabilities to the type checker."""
        raise AttributeError(name)


_status_update_tasks: set[asyncio.Task] = set()


def _schedule_status_update(server_id: int, success: bool) -> None:
    task = asyncio.create_task(update_ssh_connection_status(server_id, success))
    _status_update_tasks.add(task)
    task.add_done_callback(_status_update_tasks.discard)


async def shutdown_background_tasks() -> None:
    """Await pending SSH status writes before the database engine is disposed."""
    tasks = list(_status_update_tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _status_update_tasks.clear()


async def _cleanup_local_download_dir(download_dir: str, panel_temp_dir: str) -> None:
    """Delete a potentially large local download tree without blocking the event loop."""

    def cleanup() -> tuple[bool, bool]:
        if not os.path.exists(download_dir):
            return False, False
        shutil.rmtree(download_dir)
        parent_removed = False
        try:
            if os.path.exists(panel_temp_dir) and not os.listdir(panel_temp_dir):
                os.rmdir(panel_temp_dir)
                parent_removed = True
        except OSError:
            pass
        return True, parent_removed

    try:
        download_removed, parent_removed = await asyncio.to_thread(cleanup)
        if download_removed:
            logger.info("Cleaned up panel temp directory: %s", download_dir)
        if parent_removed:
            logger.info("Cleaned up empty parent directory: %s", panel_temp_dir)
    except Exception as exc:
        logger.warning("Failed to clean up panel temp directory %s: %s", download_dir, exc)


async def update_ssh_connection_status(server_id: int, success: bool):
    """
    Update SSH connection status tracking in database

    This is a fire-and-forget background task that should never block SSH operations.
    All exceptions are caught and logged to prevent blocking the caller.

    Args:
        server_id: Server ID
        success: Whether the SSH connection was successful
    """
    from sqlalchemy import update as sql_update
    from sqlmodel import col

    from modules.database import async_session_maker
    from modules.models import Server as ServerModel
    from modules.utils import get_current_time

    try:
        # Use a short timeout to prevent blocking
        async with asyncio.timeout(5):
            async with async_session_maker() as db:
                server = await db.get(ServerModel, server_id)
                if not server:
                    logger.warning(f"Cannot update SSH status: server {server_id} not found")
                    return

                now = get_current_time()

                if success:
                    # Reset failure tracking on successful connection
                    await db.execute(
                        sql_update(ServerModel)
                        .where(col(ServerModel.id) == server_id)
                        .values(last_ssh_success=now, consecutive_ssh_failures=0, is_ssh_down=False)
                    )
                    logger.debug(
                        f"SSH connection successful for server {server_id} - reset failure tracking"
                    )
                else:
                    # Increment failure count
                    new_failure_count = server.consecutive_ssh_failures + 1

                    # Mark as down after 3 consecutive failures (immediate, not days-based)
                    # This prevents repeated connection attempts to offline servers
                    is_down = new_failure_count >= 3

                    await db.execute(
                        sql_update(ServerModel)
                        .where(col(ServerModel.id) == server_id)
                        .values(
                            last_ssh_failure=now,
                            consecutive_ssh_failures=new_failure_count,
                            is_ssh_down=is_down,
                        )
                    )
                    if is_down:
                        logger.warning(
                            f"Server {server_id} marked as SSH down after {new_failure_count} consecutive failures"
                        )

                await db.commit()
    except asyncio.TimeoutError:
        logger.warning(f"Timeout updating SSH status for server {server_id} - database may be busy")
    except Exception as e:
        logger.error(f"Failed to update SSH connection status for server {server_id}: {e}")


__all__ = [
    "asyncio",
    "contextlib",
    "inspect",
    "ipaddress",
    "logging",
    "os",
    "posixpath",
    "re",
    "shlex",
    "shutil",
    "socket",
    "tempfile",
    "time",
    "uuid",
    "datetime",
    "Message",
    "collapse_rfc2231_value",
    "Any",
    "AsyncIterator",
    "Awaitable",
    "Callable",
    "Dict",
    "List",
    "Optional",
    "Tuple",
    "unquote",
    "urljoin",
    "urlsplit",
    "anyio",
    "asyncssh",
    "Server",
    "availability_command",
    "capture_console_command",
    "cleanup_command",
    "find_running_session_manager",
    "find_running_session_managers",
    "force_stop_session_command",
    "gslt_startup_parameter",
    "normalize_session_manager",
    "session_manager_order",
    "session_name",
    "start_session_command",
    "steamcmd_session_name",
    "stop_session_command",
    "server_monitor",
    "ssh_connection_pool",
    "logger",
    "_status_update_tasks",
    "_schedule_status_update",
    "shutdown_background_tasks",
    "_cleanup_local_download_dir",
    "update_ssh_connection_status",
    "SSHMixinBase",
]
