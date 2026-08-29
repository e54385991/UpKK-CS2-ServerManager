"""Exclusive SteamCMD deploy lock and operator force-stop signals.

Prevents two deploy/update/validate runs from starting ``steamcmd`` on the
same game server. Force-stop is a confirmed operator action: it sets a cancel
flag and the host-side killer only matches this server's game directory.
"""

from __future__ import annotations

import re
import shlex

from services.redis_manager import redis_manager

STEAMCMD_ACTIONS = frozenset({"deploy", "update", "validate"})
DEPLOYMENT_LOCK_TTL_SECONDS = 7200
DEPLOYMENT_LOCK_PREFIX = "deployment_lock"
STEAMCMD_CANCEL_PREFIX = "steamcmd_cancel"
STEAMCMD_FORCE_TERMINATED = "Force-terminated by operator"


def deployment_lock_key(server_id: int) -> str:
    return f"{DEPLOYMENT_LOCK_PREFIX}:{server_id}"


def steamcmd_cancel_key(server_id: int) -> str:
    return f"{STEAMCMD_CANCEL_PREFIX}:{server_id}"


def steamcmd_pgrep_command(game_directory: str) -> str:
    """List SteamCMD PIDs whose command line includes this server's game dir."""
    pattern = f"steamcmd.*{re.escape(game_directory)}"
    return f"pgrep -f {shlex.quote(pattern)} || true"


async def acquire_steamcmd_lock(server_id: int, token: str) -> bool | None:
    """Atomically take the per-server SteamCMD lock. ``None`` means Redis is down."""
    return await redis_manager.acquire_lock(
        deployment_lock_key(server_id), token, DEPLOYMENT_LOCK_TTL_SECONDS
    )


async def release_steamcmd_lock(server_id: int, token: str) -> bool:
    return await redis_manager.release_lock(deployment_lock_key(server_id), token)


async def force_clear_steamcmd_lock(server_id: int) -> None:
    await redis_manager.delete(deployment_lock_key(server_id))


async def steamcmd_lock_held(server_id: int) -> bool:
    return bool(await redis_manager.get(deployment_lock_key(server_id)))


async def request_steamcmd_cancel(server_id: int) -> None:
    await redis_manager.set(steamcmd_cancel_key(server_id), "1", expire=DEPLOYMENT_LOCK_TTL_SECONDS)


async def clear_steamcmd_cancel(server_id: int) -> None:
    await redis_manager.delete(steamcmd_cancel_key(server_id))


async def prepare_steamcmd_operation(server_id: int) -> None:
    """Drop leftover force-stop so a new deploy/update/validate can run SteamCMD."""
    await clear_steamcmd_cancel(server_id)


async def steamcmd_cancel_requested(server_id: int) -> bool:
    return bool(await redis_manager.get(steamcmd_cancel_key(server_id)))


def is_steamcmd_force_terminated(detail: str | None) -> bool:
    return STEAMCMD_FORCE_TERMINATED in (detail or "")


def cs2_deploy_steamcmd_failure_message(
    *,
    max_retries: int,
    executable_path: str,
    error_detail: str,
) -> str:
    """User-facing deploy failure. Operator cancel is not 'retries exhausted'."""
    if is_steamcmd_force_terminated(error_detail):
        return (
            "SteamCMD 已被操作员强制终止，部署已中断；可以重新部署。"
            " / SteamCMD was force-stopped by the operator. You can deploy again."
        )
    return (
        "🚨 CS2 部署失败报警：SteamCMD 初次执行及 "
        f"{max_retries} 次自动恢复后，"
        f"仍未找到文件 {executable_path}。部署已中断；请重新部署或运行修复。"
        f" 错误详情：{error_detail}"
    )
