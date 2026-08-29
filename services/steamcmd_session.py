"""Detached tmux/screen helpers for SteamCMD so SSH reconnect cannot kill it."""

from __future__ import annotations

import shlex

from services.game_session import steamcmd_session_name

STEAMCMD_EXIT_FILENAME = ".upkk-steamcmd-exit"


def steamcmd_exit_path(game_directory: str) -> str:
    return f"{game_directory.rstrip('/')}/{STEAMCMD_EXIT_FILENAME}"


def wrap_steamcmd_payload(command: str, exit_path: str) -> str:
    """Run SteamCMD in a login shell and persist the exit code when it finishes."""
    script = (
        f"{command}; "
        "status=$?; "
        f"printf '%s\\n' \"$status\" > {shlex.quote(exit_path)}; "
        'exit "$status"'
    )
    return f"bash -lc {shlex.quote(script)}"


def incremental_console_lines(previous: str, current: str) -> list[str]:
    """Turn a full pane snapshot into newly visible SteamCMD lines.

    SteamCMD often redraws a single progress line with ``\\r``. When the
    snapshot is replaced rather than appended, emit the latest non-empty line.
    """
    if not current or current == previous:
        return []
    if previous and current.startswith(previous):
        suffix = current[len(previous) :]
        return _visible_lines(suffix)
    return _visible_lines(current)[-3:]


def parse_steamcmd_exit_code(stdout: str) -> int | None:
    text = (stdout or "").strip().splitlines()
    if not text:
        return None
    first = text[0].strip()
    if first.isdigit():
        return int(first)
    return None


def _visible_lines(text: str) -> list[str]:
    normalized = text.replace("\r", "\n")
    return [line.strip() for line in normalized.split("\n") if line.strip()]


__all__ = [
    "STEAMCMD_EXIT_FILENAME",
    "incremental_console_lines",
    "parse_steamcmd_exit_code",
    "steamcmd_exit_path",
    "steamcmd_session_name",
    "wrap_steamcmd_payload",
]
