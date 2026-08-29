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
        download_removed, parent_removed = await anyio.to_thread.run_sync(cleanup)
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
                        .where(ServerModel.id == server_id)
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
                        .where(ServerModel.id == server_id)
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


__all__ = [name for name in globals() if not name.startswith("__")]
