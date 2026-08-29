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


def latest_console_heartbeat(text: str) -> str | None:
    """Return the latest visible pane line, including CR-only progress.

    SteamCMD often redraws one line with ``\\r`` and never appends ``\\n``.
    Callers still need a heartbeat so the UI is not stuck empty.
    """
    if not text:
        return None
    visible = _visible_lines(text)
    if visible:
        return visible[-1]
    if "\r" in text:
        last = text.replace("\n", "\r").split("\r")[-1].strip()
        if last:
            return last
        return None
    stripped = text.strip()
    return stripped or None


def incremental_console_lines(previous: str, current: str) -> list[str]:
    """Turn a full pane snapshot into newly visible SteamCMD lines.

    SteamCMD often redraws a single progress line with ``\\r``. When the
    snapshot is replaced rather than appended, emit the latest non-empty line.
    CR-only changes that strip to no ``\\n`` lines still emit a heartbeat.
    """
    if not current:
        return []
    if current == previous:
        return []
    if previous and current.startswith(previous):
        suffix = current[len(previous) :]
        lines = _visible_lines(suffix)
        if lines:
            return lines
        heartbeat = latest_console_heartbeat(current)
        return [heartbeat] if heartbeat else []
    visible = _visible_lines(current)
    if visible:
        return visible[-3:]
    heartbeat = latest_console_heartbeat(current)
    return [heartbeat] if heartbeat else []


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
    "latest_console_heartbeat",
    "parse_steamcmd_exit_code",
    "steamcmd_exit_path",
    "steamcmd_session_name",
    "wrap_steamcmd_payload",
]
