"""Fail-closed Discord Guild, channel, whitelist, and capability authorization."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from modules.models import Server, ServerDiscordBinding, User, UserDiscordBot
from modules.schemas.discord import DiscordCapability


class DiscordAuthorizationDenied(PermissionError):
    pass


def actor_allowed_by_allowlist(
    *,
    user_ids: list[str] | None,
    role_ids: list[str] | None,
    allow_channel_managers: bool,
    allow_server_administrators: bool,
    actor_user_id: str,
    actor_role_ids: set[str],
    actor_is_channel_manager: bool,
    actor_is_server_administrator: bool,
) -> bool:
    """Elevated Discord permissions count only through explicit binding switches."""

    return bool(
        actor_user_id in set(user_ids or [])
        or (actor_role_ids & set(role_ids or []))
        or (allow_channel_managers and actor_is_channel_manager)
        or (allow_server_administrators and actor_is_server_administrator)
    )


def _capability_allowed(
    capabilities: list[str] | None, required_capability: DiscordCapability | None
) -> bool:
    if required_capability is None:
        return True
    return required_capability.value in set(capabilities or [])


def _virtual_binding_from_global(bot: UserDiscordBot, server_id: int) -> ServerDiscordBinding:
    return ServerDiscordBinding(
        server_id=server_id,
        user_id=bot.user_id,
        enabled=True,
        guild_id=bot.global_guild_id,
        channel_ids=list(bot.global_channel_ids or []),
        role_ids=list(bot.global_role_ids or []),
        user_ids=list(bot.global_user_ids or []),
        allow_channel_managers=bot.global_allow_channel_managers,
        allow_server_administrators=bot.global_allow_server_administrators,
        capabilities=list(bot.global_capabilities or []),
    )


async def authorized_bindings(
    db: AsyncSession,
    *,
    bot_owner_user_id: int,
    guild_id: str,
    channel_id: str,
    actor_user_id: str,
    actor_role_ids: set[str],
    actor_is_channel_manager: bool = False,
    actor_is_server_administrator: bool = False,
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
        .join(Server, col(Server.id) == col(ServerDiscordBinding.server_id))
        .where(
            ServerDiscordBinding.user_id == bot_owner_user_id,
            col(ServerDiscordBinding.enabled).is_(True),
            col(ServerDiscordBinding.invalid_reason).is_(None),
            ServerDiscordBinding.guild_id == guild_id,
            Server.user_id == bot_owner_user_id,
        )
    )
    rows = list(result.all())
    authorized: list[tuple[ServerDiscordBinding, Server]] = []
    configured_ids: set[int] = set()
    for binding, server in rows:
        if server.id is not None:
            configured_ids.add(server.id)
        if channel_id not in set(binding.channel_ids or []):
            continue
        if not actor_allowed_by_allowlist(
            user_ids=binding.user_ids,
            role_ids=binding.role_ids,
            allow_channel_managers=binding.allow_channel_managers,
            allow_server_administrators=binding.allow_server_administrators,
            actor_user_id=actor_user_id,
            actor_role_ids=actor_role_ids,
            actor_is_channel_manager=actor_is_channel_manager,
            actor_is_server_administrator=actor_is_server_administrator,
        ):
            continue
        if not _capability_allowed(binding.capabilities, required_capability):
            continue
        authorized.append((binding, server))
    if (
        bot.global_binding_configured is not True
        or bot.global_binding_enabled is not True
        or bot.global_guild_id != guild_id
        or channel_id not in set(bot.global_channel_ids or [])
        or not actor_allowed_by_allowlist(
            user_ids=bot.global_user_ids,
            role_ids=bot.global_role_ids,
            allow_channel_managers=bot.global_allow_channel_managers,
            allow_server_administrators=bot.global_allow_server_administrators,
            actor_user_id=actor_user_id,
            actor_role_ids=actor_role_ids,
            actor_is_channel_manager=actor_is_channel_manager,
            actor_is_server_administrator=actor_is_server_administrator,
        )
        or not _capability_allowed(bot.global_capabilities, required_capability)
    ):
        return authorized
    owned = await db.execute(select(Server).where(Server.user_id == bot_owner_user_id))
    for server in owned.scalars().all():
        if server.id is None or server.id in configured_ids:
            continue
        authorized.append((_virtual_binding_from_global(bot, server.id), server))
    return authorized
