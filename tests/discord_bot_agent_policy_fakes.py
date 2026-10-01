"""Discord Gateway Bot and server-level AI authorization regressions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from modules.models import (
    AuthType,
    Server,
    ServerDiscordBinding,
    User,
    UserDiscordBot,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _auth_fixtures(
    *,
    allow_channel_managers: bool = False,
    allow_server_administrators: bool = False,
):
    bot = UserDiscordBot(user_id=1, token_encrypted="encrypted", enabled=True)
    owner = User(
        id=1,
        username="owner",
        email="owner@example.com",
        hashed_password="hash",
        is_active=True,
    )
    server = Server(
        id=10,
        user_id=1,
        name="Server",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
    )
    binding = ServerDiscordBinding(
        server_id=10,
        user_id=1,
        enabled=True,
        guild_id="100",
        channel_ids=["200"],
        role_ids=[],
        user_ids=[],
        allow_channel_managers=allow_channel_managers,
        allow_server_administrators=allow_server_administrators,
        capabilities=["status"],
    )
    db = AsyncMock()
    db.get.side_effect = [bot, owner]
    result = AsyncMock()
    result.all = lambda: [(binding, server)]
    db.execute.return_value = result
    return db, binding, server


def _global_bot() -> UserDiscordBot:
    return UserDiscordBot(
        user_id=1,
        global_binding_configured=True,
        global_binding_enabled=True,
        global_guild_id="100",
        global_channel_ids=["200"],
        global_role_ids=["300"],
        global_user_ids=["400"],
        global_capabilities=["status", "restart"],
    )


def _discord_http_error(code: int, message: str = "Unknown Message", status: int = 404):
    response = SimpleNamespace(status=status, reason="Not Found")
    return discord.HTTPException(response, {"code": code, "message": message})
