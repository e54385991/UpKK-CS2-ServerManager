"""Normalize and compare game-server install directories."""

from __future__ import annotations

import posixpath
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from modules.models import Server

HOST_DIRECTORY_EXISTS_CODE = "host_directory_exists"


def normalize_game_directory(value: str) -> str:
    """Return a safe absolute game directory for comparisons and storage."""

    raw = str(value or "").strip()
    if not raw.startswith("/"):
        raise ValueError("Game directory must be an absolute path")
    raw = "/" + raw.lstrip("/")
    normalized = posixpath.normpath(raw)
    if normalized == "/":
        raise ValueError("Game directory cannot be the filesystem root")
    if len(normalized) > 500:
        raise ValueError("Game directory must be at most 500 characters")
    return normalized


def host_directory_conflict_payload(
    server: Server, host: str, game_directory: str
) -> dict[str, Any]:
    """Structured 409 body when a panel server already occupies host+directory."""

    return {
        "code": HOST_DIRECTORY_EXISTS_CODE,
        "message": (
            f"A server named '{server.name}' already uses host ({host}) "
            f"and game directory ({game_directory}). "
            "Choose a different directory or redeploy that server."
        ),
        "existing_server_id": server.id,
        "existing_server_name": server.name,
        "host": host,
        "game_directory": game_directory,
    }


def _stored_directory(value: str) -> str:
    try:
        return normalize_game_directory(value)
    except ValueError:
        stripped = str(value or "").strip().rstrip("/")
        return stripped or str(value or "")


async def find_host_directory_server(
    db: AsyncSession, host: str, game_directory: str, user_id: int
) -> Server | None:
    """Return the caller's server on this host whose directory matches after normalize."""

    normalized = normalize_game_directory(game_directory)
    result = await db.execute(select(Server).where(Server.user_id == user_id, Server.host == host))
    for server in result.scalars().all():
        if _stored_directory(server.game_directory) == normalized:
            return server
    return None
