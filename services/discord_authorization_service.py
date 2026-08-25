"""Fail-closed Discord Guild, channel, whitelist, and capability authorization."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from modules.models import Server, ServerDiscordBinding, User, UserDiscordBot
from modules.schemas.discord import DiscordCapability


class DiscordAuthorizationDenied(PermissionError):
    pass


async def authorized_bindings(
    db: AsyncSession,
    *,
    bot_owner_user_id: int,
    guild_id: str,
    channel_id: str,
    actor_user_id: str,
    actor_role_ids: set[str],
    actor_is_channel_manager: bool = False,
    required_capability: DiscordCapability | None = None,
) -> list[tuple[ServerDiscordBinding, Server]]:
    bot = await db.get(UserDiscordBot, bot_owner_user_id)
    owner = await db.get(User, bot_owner_user_id)
    if (
        bot is None
        or owner is None
        or not owner.is_active
        or not bot.enabled
        or not bot.token_encrypted
    ):
        raise DiscordAuthorizationDenied("Discord Bot is disabled")
    result = await db.execute(
        select(ServerDiscordBinding, Server)
        .join(Server, Server.id == ServerDiscordBinding.server_id)
        .where(
            ServerDiscordBinding.user_id == bot_owner_user_id,
            ServerDiscordBinding.enabled.is_(True),
            ServerDiscordBinding.invalid_reason.is_(None),
            ServerDiscordBinding.guild_id == guild_id,
            Server.user_id == bot_owner_user_id,
        )
    )
    authorized: list[tuple[ServerDiscordBinding, Server]] = []
    for binding, server in result.all():
        if channel_id not in set(binding.channel_ids or []):
            continue
        user_match = actor_user_id in set(binding.user_ids or [])
        role_match = bool(actor_role_ids & set(binding.role_ids or []))
        channel_manager_match = binding.allow_channel_managers and actor_is_channel_manager
        # Elevated Discord permissions are considered only through the explicit,
        # binding-scoped channel-manager switch. There is no implicit bypass.
        if not user_match and not role_match and not channel_manager_match:
            continue
        if required_capability is not None and required_capability.value not in set(
            binding.capabilities or []
        ):
            continue
        authorized.append((binding, server))
    return authorized
