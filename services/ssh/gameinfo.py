"""Remote ``gameinfo.gi`` SearchPaths updates over an existing SSH session."""

from __future__ import annotations

import base64
import shlex
from collections.abc import Awaitable, Callable

from services.gameinfo_gi import (
    GAMEINFO_METAMOD_PATH,
    GAMEINFO_SWIFTLY_PATH,
    GameinfoRewriteResult,
    rewrite_gameinfo_search_paths,
)

ExecuteCommand = Callable[..., Awaitable[tuple[bool, str, str]]]


async def _remote_dir_exists(execute_command: ExecuteCommand, path: str) -> bool:
    quoted = shlex.quote(path)
    success, stdout, _ = await execute_command(f"test -d {quoted} && echo 'exists'")
    return bool(success and "exists" in stdout)


async def _write_remote_text(
    execute_command: ExecuteCommand,
    path: str,
    content: str,
) -> bool:
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    quoted_path = shlex.quote(path)
    command = (
        f"printf '%s' {shlex.quote(encoded)} | base64 -d > {quoted_path}.tmp "
        f"&& mv {quoted_path}.tmp {quoted_path}"
    )
    success, _, _ = await execute_command(command)
    return success


def _write_failure_message(*, include_metamod: bool, include_swiftly: bool) -> str:
    if include_metamod and not include_swiftly:
        return "Metamod gameinfo.gi configuration verification failed"
    if include_swiftly and not include_metamod:
        return "SwiftlyS2 gameinfo.gi configuration verification failed"
    return "gameinfo.gi SearchPaths configuration verification failed"


async def ensure_remote_gameinfo_search_paths(
    execute_command: ExecuteCommand,
    gameinfo_path: str,
    *,
    include_metamod: bool = False,
    include_swiftly: bool = False,
    detect_metamod_dir: str | None = None,
    detect_swiftly_dir: str | None = None,
    backup_suffix: str = ".backup",
) -> tuple[bool, str, GameinfoRewriteResult | None]:
    """Read, rewrite, and write ``gameinfo.gi`` using official Loader order.

    ``include_*`` forces a SearchPath on. ``detect_*_dir`` also turns the matching
    path on when that addon directory already exists, so installing SwiftlyS2
    after a panel Metamod install (or the reverse) keeps both lines and puts
    Metamod first.
    """
    quoted = shlex.quote(gameinfo_path)
    exists_ok, exists_out, _ = await execute_command(f"test -f {quoted} && echo 'exists'")
    if not exists_ok or "exists" not in exists_out:
        return False, "gameinfo.gi not found. Server may not be properly installed.", None

    if detect_metamod_dir:
        include_metamod = include_metamod or await _remote_dir_exists(
            execute_command, detect_metamod_dir
        )
    if detect_swiftly_dir:
        include_swiftly = include_swiftly or await _remote_dir_exists(
            execute_command, detect_swiftly_dir
        )

    read_ok, content, read_err = await execute_command(f"cat {quoted}")
    if not read_ok:
        return False, f"Failed to read gameinfo.gi: {read_err or 'cat failed'}", None

    result = rewrite_gameinfo_search_paths(
        content,
        include_metamod=include_metamod,
        include_swiftly=include_swiftly,
    )
    if result.missing_anchor:
        return (
            False,
            "gameinfo.gi is missing the Game_LowViolence line required by Metamod and SwiftlyS2",
            result,
        )
    if not result.changed:
        return True, "already configured", result

    if backup_suffix:
        backup_path = shlex.quote(f"{gameinfo_path}{backup_suffix}")
        await execute_command(f"cp {quoted} {backup_path}")

    if not await _write_remote_text(execute_command, gameinfo_path, result.content):
        return (
            False,
            _write_failure_message(
                include_metamod=include_metamod, include_swiftly=include_swiftly
            ),
            result,
        )
    return True, "updated", result


def gameinfo_progress_detail(result: GameinfoRewriteResult | None) -> str:
    """Human-readable SearchPaths summary for installer logs."""
    if result is None:
        return GAMEINFO_METAMOD_PATH
    parts: list[str] = []
    if result.has_metamod:
        parts.append(f"Game {GAMEINFO_METAMOD_PATH}")
    if result.has_swiftly:
        parts.append(f"Game {GAMEINFO_SWIFTLY_PATH}")
    return " then ".join(parts) if parts else "SearchPaths"


# Re-export paths so callers do not import the rewrite module just for constants.
__all__ = [
    "GAMEINFO_METAMOD_PATH",
    "GAMEINFO_SWIFTLY_PATH",
    "ensure_remote_gameinfo_search_paths",
    "gameinfo_progress_detail",
]
