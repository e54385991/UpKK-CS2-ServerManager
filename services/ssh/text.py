"""Decode bytes from SSH and game/SteamCMD panes without failing the session.

CS2 and tmux capture often mix UTF-8 with GBK, Windows-1252, or broken
escape sequences. A strict UTF-8 decode raises and closes the console.
"""

from __future__ import annotations


def decode_remote_text(value: bytes | str | None) -> str:
    """Return text, replacing undecodable bytes instead of raising."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return bytes(value).decode("utf-8", errors="replace")


def encode_console_input(value: str) -> bytes:
    return value.encode("utf-8", errors="replace")
