"""Detached tmux/screen helpers for SteamCMD so SSH reconnect cannot kill it."""

from __future__ import annotations

import re
import shlex

from services.game_session import steamcmd_session_name

STEAMCMD_EXIT_FILENAME = ".upkk-steamcmd-exit"

_STEAMCMD_PROGRESS = re.compile(
    r"Update state \(0x[0-9a-f]+\) downloading, progress:",
    re.IGNORECASE,
)
_PROGRESS_LINE = re.compile(
    r"(?:Update state \(0x[0-9a-f]+\) downloading,\s+)?"
    r"progress:\s+\d+(?:\.\d+)?\s+\(\s*\d+\s*/\s*\d+\s*\)",
    re.IGNORECASE,
)
_PROGRESS_VALUES = re.compile(
    r"progress:\s+(\d+(?:\.\d+)?)\s+\(\s*(\d+)\s*/\s*(\d+)",
    re.IGNORECASE,
)
_WRAP_TAIL = re.compile(r"^(?:/\s*)?\d+\)$")
_PROGRESS_WRAP_HEAD = re.compile(
    r"(?:progress:\s+\d+(?:\.\d+)?\s+)?\(\d+\s*/\s*\d*$|"
    r"progress:\s+\d+(?:\.\d+)?\s+\(\d+\s*/\s*\d*$|"
    r"Update state \(0x[0-9a-f]+\) downloading,(?:\s+progress:)?\s*$",
    re.IGNORECASE,
)
_PROGRESS_CONTINUATION = re.compile(
    r"^(?:progress:\s+)?\d+(?:\.\d+)?(?:\s+\(|$)|"
    r"^\(\d+|"
    r"^/\s*\d+",
    re.IGNORECASE,
)


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


def _visible_lines(text: str) -> list[str]:
    normalized = text.replace("\r", "\n")
    return [line.strip() for line in normalized.split("\n") if line.strip()]


def _collapse_wrapped_progress(lines: list[str]) -> list[str]:
    """Join a SteamCMD wrap leftover such as ``54542)`` back onto the progress line.

    An 80-column pane splits ``... / 71089554542)`` so the web log treats the
    stable total-bytes tail as the "latest" line and looks frozen.
    """
    collapsed: list[str] = []
    for line in lines:
        if (
            collapsed
            and _PROGRESS_WRAP_HEAD.search(collapsed[-1])
            and (_WRAP_TAIL.fullmatch(line) or _PROGRESS_CONTINUATION.match(line))
        ):
            joiner = "" if _WRAP_TAIL.fullmatch(line) else " "
            collapsed[-1] = f"{collapsed[-1]}{joiner}{line}".replace("  ", " ")
            continue
        collapsed.append(line)
    return collapsed


def _snapshot_lines(text: str) -> list[str]:
    return _collapse_wrapped_progress(_visible_lines(text))


def _progress_matches(text: str) -> list[tuple[int, str]]:
    """Find download progress even when tmux wrapped it across lines.

    Prefer the match with the largest downloaded-byte count so an old complete
    10% line cannot beat a wrapped 50% line that no longer matches in-place.
    """
    flat = re.sub(r"[\r\n]+", " ", text)
    found: list[tuple[int, str]] = []
    for match in _PROGRESS_LINE.finditer(flat):
        values = _PROGRESS_VALUES.search(match.group(0))
        if values is None:
            continue
        found.append((int(values.group(2)), re.sub(r"\s+", " ", match.group(0)).strip()))
    return found


def _latest_progress_line(lines: list[str]) -> str | None:
    ranked = _progress_matches("\n".join(lines))
    if ranked:
        return max(ranked, key=lambda item: item[0])[1]
    for line in reversed(lines):
        if _STEAMCMD_PROGRESS.search(line):
            return line
    return None


def latest_console_heartbeat(text: str) -> str | None:
    """Return the latest visible pane line, including CR-only progress.

    SteamCMD often redraws one line with ``\\r`` and never appends ``\\n``.
    Callers still need a heartbeat so the UI is not stuck empty.
    Prefer a progress line over a wrap remnant such as ``54542)``.
    """
    if not text:
        return None
    ranked = _progress_matches(text)
    if ranked:
        return max(ranked, key=lambda item: item[0])[1]
    visible = _snapshot_lines(text)
    progress = _latest_progress_line(visible)
    if progress:
        return progress
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

    SteamCMD redraws one progress line with ``\\r``. tmux capture then looks
    replaced rather than appended. Only emit the changed progress / newest
    line — never the last three history lines, which re-plays older
    percentages and looks like the page froze or went backwards.
    """
    if not current or current == previous:
        return []
    prev_ranked = _progress_matches(previous)
    curr_ranked = _progress_matches(current)
    prev_best = max(prev_ranked, key=lambda item: item[0]) if prev_ranked else None
    curr_best = max(curr_ranked, key=lambda item: item[0]) if curr_ranked else None
    if curr_best and (prev_best is None or curr_best[0] > prev_best[0]):
        return [curr_best[1]]
    prev_lines = _snapshot_lines(previous)
    curr_lines = _snapshot_lines(current)
    if curr_lines == prev_lines:
        return []
    if (
        prev_lines
        and len(curr_lines) >= len(prev_lines)
        and curr_lines[: len(prev_lines)] == prev_lines
    ):
        return curr_lines[len(prev_lines) :]

    prev_progress = _latest_progress_line(prev_lines)
    curr_progress = _latest_progress_line(curr_lines)
    if curr_progress and curr_progress != prev_progress:
        return [curr_progress]

    curr_last = curr_lines[-1] if curr_lines else ""
    prev_last = prev_lines[-1] if prev_lines else ""
    if curr_last and curr_last != prev_last:
        return [curr_last]
    return []


def parse_steamcmd_exit_code(stdout: str) -> int | None:
    text = (stdout or "").strip().splitlines()
    if not text:
        return None
    first = text[0].strip()
    if first.isdigit():
        return int(first)
    return None


__all__ = [
    "STEAMCMD_EXIT_FILENAME",
    "incremental_console_lines",
    "latest_console_heartbeat",
    "parse_steamcmd_exit_code",
    "steamcmd_exit_path",
    "steamcmd_session_name",
    "wrap_steamcmd_payload",
]
