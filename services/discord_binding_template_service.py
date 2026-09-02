"""Per-user Discord binding template inheritance and explicit bulk synchronization."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from modules.models import Server, ServerDiscordBinding, UserDiscordBot


def _copy_template(binding: ServerDiscordBinding, bot: UserDiscordBot) -> None:
    binding.user_id = bot.user_id
    binding.enabled = bot.global_binding_enabled
    binding.guild_id = bot.global_guild_id
    binding.channel_ids = list(bot.global_channel_ids or [])
    binding.role_ids = list(bot.global_role_ids or [])
    binding.user_ids = list(bot.global_user_ids or [])
    binding.allow_channel_managers = bot.global_allow_channel_managers
    binding.allow_server_administrators = bot.global_allow_server_administrators
    binding.capabilities = list(bot.global_capabilities or [])
    binding.response_visibility = "public"
    binding.invalid_reason = None


def binding_matches_template(binding: ServerDiscordBinding, bot: UserDiscordBot) -> bool:
    return (
        binding.user_id == bot.user_id
        and binding.enabled == bot.global_binding_enabled
        and binding.guild_id == bot.global_guild_id
        and set(binding.channel_ids or []) == set(bot.global_channel_ids or [])
        and set(binding.role_ids or []) == set(bot.global_role_ids or [])
        and set(binding.user_ids or []) == set(bot.global_user_ids or [])
        and binding.allow_channel_managers == bot.global_allow_channel_managers
        and binding.allow_server_administrators == bot.global_allow_server_administrators
        and set(binding.capabilities or []) == set(bot.global_capabilities or [])
        and binding.response_visibility == "public"
    )


async def inherit_global_discord_binding(
    db: AsyncSession, server: Server
) -> ServerDiscordBinding | None:
    """Attach the saved template to one newly created owned server without committing."""

    if server.id is None:
        raise ValueError("Server must be flushed before inheriting Discord settings")
    bot = await db.get(UserDiscordBot, server.user_id)
    if bot is None or not bot.global_binding_configured:
        return None
    binding = await db.get(ServerDiscordBinding, server.id)
    if binding is None:
        binding = ServerDiscordBinding(server_id=server.id, user_id=server.user_id)
    _copy_template(binding, bot)
    db.add(binding)
    return binding


async def sync_global_discord_binding(db: AsyncSession, bot: UserDiscordBot) -> int:
    """Explicitly overwrite every current server binding owned by the template owner."""

    result = await db.execute(select(Server).where(Server.user_id == bot.user_id))
    servers = list(result.scalars().all())
    if not servers:
        return 0
    server_ids = [server.id for server in servers if server.id is not None]
    binding_result = await db.execute(
        select(ServerDiscordBinding).where(col(ServerDiscordBinding.server_id).in_(server_ids))
    )
    bindings = {binding.server_id: binding for binding in binding_result.scalars().all()}
    for server in servers:
        if server.id is None:
            continue
        binding = bindings.get(server.id)
        if binding is None:
            binding = ServerDiscordBinding(server_id=server.id, user_id=bot.user_id)
        _copy_template(binding, bot)
        db.add(binding)
    return len(server_ids)


async def global_binding_counts(db: AsyncSession, bot: UserDiscordBot) -> tuple[int, int]:
    result = await db.execute(select(Server.id).where(Server.user_id == bot.user_id))
    server_ids = list(result.scalars().all())
    if not server_ids or not bot.global_binding_configured:
        return len(server_ids), 0
    binding_result = await db.execute(
        select(ServerDiscordBinding).where(col(ServerDiscordBinding.server_id).in_(server_ids))
    )
    matching = sum(
        binding_matches_template(binding, bot) for binding in binding_result.scalars().all()
    )
    return len(server_ids), matching
