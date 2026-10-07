"""Focused route implementation; the original module retains patchable dependencies."""

from __future__ import annotations

from fastapi import Request
from sqlalchemy import Select

from api.dependencies import ActiveUser, DatabaseSession
from modules import (
    DiscordBotOptionsResponse,
    DiscordBotSettingsResponse,
    DiscordBotSettingsUpdate,
    DiscordBotTestRequest,
    DiscordBotTestResponse,
    DiscordGlobalBindingResponse,
    UserDiscordBot,
)
from modules.models import Server, ServerDiscordBinding
from services.compat import LateBoundModule

host = LateBoundModule("api.routes.discord_bot")
router = host.router


async def _load_discord_options(
    user_id: int, token: str, guild_id: str | None = None
) -> tuple[list[dict], list[dict], list[dict]]:
    """Prefer the connected Gateway cache, with Discord REST as a safe fallback."""

    from services.discord_bot_manager import discord_bot_manager

    cached = discord_bot_manager.configuration_options(user_id, guild_id)
    if cached is not None:
        if cached.get("guild_missing"):
            raise host.DiscordBotAPIError("The Bot is not a member of the selected Guild")
        return cached["guilds"], cached["channels"], cached["roles"]
    guilds = await host.list_guilds(token)
    if guild_id is None:
        return guilds, [], []
    channels, roles = await host.get_guild_options(token, guild_id)
    return guilds, channels, roles


def _bot_response(bot: UserDiscordBot | None) -> DiscordBotSettingsResponse:
    mode = host._trigger_mode(bot.message_trigger_mode if bot is not None else None)
    return host.DiscordBotSettingsResponse(
        enabled=bool(bot and bot.enabled),
        token_configured=bool(bot and bot.token_encrypted),
        message_trigger_mode=mode,
        application_id=bot.application_id if bot else None,
        bot_user_id=bot.bot_user_id if bot else None,
        username=bot.username if bot else None,
        discriminator=bot.discriminator if bot else None,
        connection_status=bot.connection_status if bot else "not_configured",
        last_connected_at=bot.last_connected_at if bot else None,
        last_error=bot.last_error if bot else None,
        invite_url=host.build_invite_url(bot.application_id if bot else None),
    )


async def _stored_token(db: DatabaseSession, user_id: int) -> tuple[UserDiscordBot, str]:
    bot = await db.get(host.UserDiscordBot, user_id)
    if bot is None or not bot.token_encrypted:
        raise host.HTTPException(status_code=409, detail="Discord Bot Token is not configured")
    token = host.decrypt_credential(bot.token_encrypted)
    if not token:
        raise host.HTTPException(status_code=409, detail="Discord Bot Token is unavailable")
    return bot, token


async def _connected_bot_token(db: DatabaseSession, user_id: int) -> tuple[UserDiscordBot, str]:
    bot, token = await host._stored_token(db, user_id)
    if not bot.enabled or bot.connection_status != "connected":
        raise host.HTTPException(
            status_code=409,
            detail="Discord Bot must be enabled and connected before pushing a menu",
        )
    return bot, token


async def _bound_menu_push_channels(db: DatabaseSession, user_id: int) -> dict[str, set[str]]:
    statement: Select[ServerDiscordBinding, Server] = (
        host.select(host.ServerDiscordBinding, host.Server)
        .join(
            host.Server, host.col(host.Server.id) == host.col(host.ServerDiscordBinding.server_id)
        )
        .where(
            host.ServerDiscordBinding.user_id == user_id,
            host.col(host.ServerDiscordBinding.enabled).is_(True),
            host.Server.user_id == user_id,
        )
    )
    result = await db.execute(statement)
    known_capabilities = {item.value for item in host.DiscordCapability}
    channels_by_guild: dict[str, set[str]] = {}
    for binding, server in result.all():
        if (
            server.user_id != user_id
            or binding.user_id != user_id
            or not binding.guild_id
            or binding.invalid_reason is not None
            or not known_capabilities.intersection(binding.capabilities or [])
        ):
            continue
        channels_by_guild.setdefault(binding.guild_id, set()).update(binding.channel_ids or [])
    bot = await db.get(host.UserDiscordBot, user_id)
    if (
        bot is not None
        and bot.global_binding_configured is True
        and bot.global_binding_enabled is True
        and isinstance(bot.global_guild_id, str)
        and bot.global_guild_id
        and known_capabilities.intersection(bot.global_capabilities or [])
    ):
        channels_by_guild.setdefault(bot.global_guild_id, set()).update(
            str(item) for item in (bot.global_channel_ids or []) if item
        )
    return channels_by_guild


async def _validate_binding_selection(
    user_id: int,
    token: str,
    *,
    guild_id: str | None,
    channel_ids: list[str],
    role_ids: list[str],
) -> None:
    if guild_id is None:
        return
    try:
        _guilds, channels, roles = await host._load_discord_options(user_id, token, guild_id)
    except host.DiscordBotAPIError as exc:
        raise host.HTTPException(status_code=400, detail=str(exc)) from exc
    valid_channels = {
        str(item["id"])
        for item in channels
        if int(item.get("type", -1)) in host.DISCORD_COMMAND_CHANNEL_TYPES
    }
    valid_roles = {str(item["id"]) for item in roles if str(item["id"]) != guild_id}
    if set(channel_ids) - valid_channels:
        raise host.HTTPException(status_code=422, detail="One or more channels are invalid")
    if set(role_ids) - valid_roles:
        raise host.HTTPException(status_code=422, detail="One or more roles are invalid")


async def _discord_options_response(
    user_id: int, token: str, guild_id: str | None
) -> DiscordBotOptionsResponse:
    try:
        guild_payload, channels, roles = await host._load_discord_options(user_id, token, guild_id)
    except host.DiscordBotAPIError as exc:
        raise host.HTTPException(status_code=400, detail=str(exc)) from exc
    guilds = [
        host.DiscordGuildOption(
            id=str(item["id"]), name=str(item.get("name") or item["id"]), icon=item.get("icon")
        )
        for item in guild_payload
    ]
    if guild_id is None:
        return host.DiscordBotOptionsResponse(guilds=guilds)
    return host.DiscordBotOptionsResponse(
        guilds=guilds,
        channels=[
            host.DiscordChannelOption(
                id=str(item["id"]),
                guild_id=guild_id,
                name=str(item.get("name") or item["id"]),
                type=int(item.get("type", 0)),
            )
            for item in channels
            if int(item.get("type", -1)) in host.DISCORD_COMMAND_CHANNEL_TYPES
        ],
        roles=[
            host.DiscordRoleOption(
                id=str(item["id"]),
                guild_id=guild_id,
                name=str(item.get("name") or item["id"]),
                position=int(item.get("position", 0)),
            )
            for item in roles
            if str(item["id"]) != guild_id
        ],
    )


async def _global_binding_response(
    db: DatabaseSession,
    bot: UserDiscordBot,
    *,
    synced_server_count: int = 0,
) -> DiscordGlobalBindingResponse:
    server_count, matching_server_count = await host.global_binding_counts(db, bot)
    return host.DiscordGlobalBindingResponse(
        configured=bot.global_binding_configured,
        enabled=bot.global_binding_enabled,
        guild_id=bot.global_guild_id,
        channel_ids=list(bot.global_channel_ids or []),
        role_ids=list(bot.global_role_ids or []),
        user_ids=list(bot.global_user_ids or []),
        allow_channel_managers=bot.global_allow_channel_managers,
        allow_server_administrators=bot.global_allow_server_administrators,
        capabilities=[
            capability
            for value in bot.global_capabilities or []
            if (capability := host._discord_capability(value)) is not None
        ],
        server_count=server_count,
        matching_server_count=matching_server_count,
        synced_server_count=synced_server_count,
    )


@router.get("/api/auth/discord-bot", response_model=DiscordBotSettingsResponse)
async def get_discord_bot(
    db: DatabaseSession, current_user: ActiveUser
) -> DiscordBotSettingsResponse:
    return host._bot_response(await db.get(host.UserDiscordBot, current_user.id))


@router.put("/api/auth/discord-bot", response_model=DiscordBotSettingsResponse)
async def update_discord_bot(
    request: DiscordBotSettingsUpdate,
    db: DatabaseSession,
    current_user: ActiveUser,
    http_request: Request,
) -> DiscordBotSettingsResponse:
    bot = await db.get(host.UserDiscordBot, current_user.id)
    if bot is None:
        bot = host.UserDiscordBot(user_id=current_user.id)
    if request.token is not None:
        try:
            identity = await host.test_bot_token(request.token)
        except host.DiscordBotAPIError as exc:
            raise host.HTTPException(status_code=400, detail=str(exc)) from exc
        duplicate = await db.execute(
            host.select(host.UserDiscordBot).where(
                host.UserDiscordBot.user_id != current_user.id,
                host.or_(
                    host.col(host.UserDiscordBot.application_id) == identity.application_id,
                    host.col(host.UserDiscordBot.bot_user_id) == identity.bot_user_id,
                ),
            )
        )
        if duplicate.scalar_one_or_none() is not None:
            raise host.HTTPException(
                status_code=host.status.HTTP_409_CONFLICT,
                detail="This Discord Bot is already configured by another panel user",
            )
        bot.token_encrypted = host.encrypt_credential(request.token)
        bot.application_id = identity.application_id
        bot.bot_user_id = identity.bot_user_id
        bot.username = identity.username
        bot.discriminator = identity.discriminator
        bot.last_error = None
        bot.connection_status = "configured"
    if request.enabled is not None:
        if request.enabled and not bot.token_encrypted:
            raise host.HTTPException(
                status_code=409, detail="Configure a Bot Token before enabling"
            )
        bot.enabled = request.enabled
        if not request.enabled:
            bot.connection_status = "disabled"
    if request.message_trigger_mode is not None:
        bot.message_trigger_mode = request.message_trigger_mode
    db.add(bot)
    await db.commit()
    await db.refresh(bot)
    await host._notify_manager(current_user.id)
    await host.record_audit_event(
        category="settings",
        action="discord.bot.update",
        status="success",
        user=current_user,
        request=http_request,
        details={
            "token_updated": request.token is not None,
            "enabled": request.enabled,
            "message_trigger_mode": request.message_trigger_mode,
        },
    )
    return host._bot_response(bot)


@router.delete("/api/auth/discord-bot", response_model=DiscordBotSettingsResponse)
async def delete_discord_bot(
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
) -> DiscordBotSettingsResponse:
    bot = await db.get(host.UserDiscordBot, current_user.id)
    if bot is None:
        return host._bot_response(None)
    bot.token_encrypted = None
    bot.enabled = False
    bot.application_id = None
    bot.bot_user_id = None
    bot.connection_status = "not_configured"
    bot.last_error = None
    result = await db.execute(
        host.select(host.ServerDiscordBinding).where(
            host.ServerDiscordBinding.user_id == current_user.id
        )
    )
    for binding in result.scalars().all():
        binding.invalid_reason = "bot_token_missing"
        db.add(binding)
    db.add(bot)
    await db.commit()
    await host._notify_manager(current_user.id)
    await host.record_audit_event(
        category="settings",
        action="discord.bot.delete",
        status="success",
        user=current_user,
        request=request,
    )
    return host._bot_response(bot)


@router.post("/api/auth/discord-bot/test", response_model=DiscordBotTestResponse)
async def test_discord_bot(
    request: DiscordBotTestRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> DiscordBotTestResponse:
    token = request.token
    if token is None:
        _bot, token = await host._stored_token(db, current_user.id)
    try:
        identity = await host.test_bot_token(token)
    except host.DiscordBotAPIError as exc:
        return host.DiscordBotTestResponse(success=False, message=str(exc))
    return host.DiscordBotTestResponse(
        success=True,
        application_id=identity.application_id,
        bot_user_id=identity.bot_user_id,
        username=identity.username,
        message="Discord Bot Token is valid",
    )
