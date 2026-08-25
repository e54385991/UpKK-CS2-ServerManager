"""Per-user Discord Bot and server-scoped Discord/AI permission APIs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_
from sqlmodel import select

from api.dependencies import ActiveUser, DatabaseSession, require_server_access
from modules import (
    AgentPolicyResponse,
    AgentPolicyUpdate,
    DiscordBindingResponse,
    DiscordBindingUpdate,
    DiscordBotOptionsResponse,
    DiscordBotSettingsResponse,
    DiscordBotSettingsUpdate,
    DiscordBotTestRequest,
    DiscordBotTestResponse,
    DiscordCapability,
    DiscordChannelOption,
    DiscordGlobalBindingResponse,
    DiscordGlobalBindingUpdate,
    DiscordGuildOption,
    DiscordMenuPushOptionsResponse,
    DiscordMenuPushRequest,
    DiscordMenuPushResponse,
    DiscordRoleOption,
    Server,
    ServerAgentPolicy,
    ServerDiscordBinding,
    User,
    UserDiscordBot,
)
from services.agent_policy_service import get_effective_agent_policy
from services.ai_security import decrypt_credential, encrypt_credential, get_effective_provider
from services.discord_binding_template_service import (
    global_binding_counts,
    sync_global_discord_binding,
)
from services.discord_bot_service import (
    DISCORD_COMMAND_CHANNEL_TYPES,
    DISCORD_MENU_PUSH_CHANNEL_TYPES,
    DiscordBotAPIError,
    build_invite_url,
    delete_menu_launcher_after,
    get_guild_locale,
    get_guild_options,
    list_guilds,
    send_menu_launcher,
    test_bot_token,
)
from services.redis_manager import redis_manager
from services.task_registry import discord_menu_task_registry

router = APIRouter(tags=["discord-bot"])


async def _notify_manager(user_id: int) -> None:
    from services.discord_bot_manager import discord_bot_manager

    await discord_bot_manager.reconcile_user(user_id)


async def _load_discord_options(
    user_id: int, token: str, guild_id: str | None = None
) -> tuple[list[dict], list[dict], list[dict]]:
    """Prefer the connected Gateway cache, with Discord REST as a safe fallback."""

    from services.discord_bot_manager import discord_bot_manager

    cached = discord_bot_manager.configuration_options(user_id, guild_id)
    if cached is not None:
        if cached.get("guild_missing"):
            raise DiscordBotAPIError("The Bot is not a member of the selected Guild")
        return cached["guilds"], cached["channels"], cached["roles"]
    guilds = await list_guilds(token)
    if guild_id is None:
        return guilds, [], []
    channels, roles = await get_guild_options(token, guild_id)
    return guilds, channels, roles


def _bot_response(bot: UserDiscordBot | None) -> DiscordBotSettingsResponse:
    return DiscordBotSettingsResponse(
        enabled=bool(bot and bot.enabled),
        token_configured=bool(bot and bot.token_encrypted),
        message_trigger_mode=bot.message_trigger_mode if bot else "mention_only",
        application_id=bot.application_id if bot else None,
        bot_user_id=bot.bot_user_id if bot else None,
        username=bot.username if bot else None,
        discriminator=bot.discriminator if bot else None,
        connection_status=bot.connection_status if bot else "not_configured",
        last_connected_at=bot.last_connected_at if bot else None,
        last_error=bot.last_error if bot else None,
        invite_url=build_invite_url(bot.application_id if bot else None),
    )


async def _stored_token(db: DatabaseSession, user_id: int) -> tuple[UserDiscordBot, str]:
    bot = await db.get(UserDiscordBot, user_id)
    if bot is None or not bot.token_encrypted:
        raise HTTPException(status_code=409, detail="Discord Bot Token is not configured")
    token = decrypt_credential(bot.token_encrypted)
    if not token:
        raise HTTPException(status_code=409, detail="Discord Bot Token is unavailable")
    return bot, token


async def _connected_bot_token(db: DatabaseSession, user_id: int) -> tuple[UserDiscordBot, str]:
    bot, token = await _stored_token(db, user_id)
    if not bot.enabled or bot.connection_status != "connected":
        raise HTTPException(
            status_code=409,
            detail="Discord Bot must be enabled and connected before pushing a menu",
        )
    return bot, token


async def _bound_menu_push_channels(db: DatabaseSession, user_id: int) -> dict[str, set[str]]:
    result = await db.execute(
        select(ServerDiscordBinding, Server)
        .join(Server, Server.id == ServerDiscordBinding.server_id)
        .where(
            ServerDiscordBinding.user_id == user_id,
            ServerDiscordBinding.enabled.is_(True),
            Server.user_id == user_id,
        )
    )
    known_capabilities = {item.value for item in DiscordCapability}
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
        _guilds, channels, roles = await _load_discord_options(user_id, token, guild_id)
    except DiscordBotAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    valid_channels = {
        str(item["id"])
        for item in channels
        if int(item.get("type", -1)) in DISCORD_COMMAND_CHANNEL_TYPES
    }
    valid_roles = {str(item["id"]) for item in roles if str(item["id"]) != guild_id}
    if set(channel_ids) - valid_channels:
        raise HTTPException(status_code=422, detail="One or more channels are invalid")
    if set(role_ids) - valid_roles:
        raise HTTPException(status_code=422, detail="One or more roles are invalid")


async def _discord_options_response(
    user_id: int, token: str, guild_id: str | None
) -> DiscordBotOptionsResponse:
    try:
        guild_payload, channels, roles = await _load_discord_options(user_id, token, guild_id)
    except DiscordBotAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    guilds = [
        DiscordGuildOption(
            id=str(item["id"]), name=str(item.get("name") or item["id"]), icon=item.get("icon")
        )
        for item in guild_payload
    ]
    if guild_id is None:
        return DiscordBotOptionsResponse(guilds=guilds)
    return DiscordBotOptionsResponse(
        guilds=guilds,
        channels=[
            DiscordChannelOption(
                id=str(item["id"]),
                guild_id=guild_id,
                name=str(item.get("name") or item["id"]),
                type=int(item.get("type", 0)),
            )
            for item in channels
            if int(item.get("type", -1)) in DISCORD_COMMAND_CHANNEL_TYPES
        ],
        roles=[
            DiscordRoleOption(
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
    server_count, matching_server_count = await global_binding_counts(db, bot)
    return DiscordGlobalBindingResponse(
        configured=bot.global_binding_configured,
        enabled=bot.global_binding_enabled,
        guild_id=bot.global_guild_id,
        channel_ids=list(bot.global_channel_ids or []),
        role_ids=list(bot.global_role_ids or []),
        user_ids=list(bot.global_user_ids or []),
        allow_channel_managers=bot.global_allow_channel_managers,
        capabilities=list(bot.global_capabilities or []),
        server_count=server_count,
        matching_server_count=matching_server_count,
        synced_server_count=synced_server_count,
    )


@router.get("/api/auth/discord-bot", response_model=DiscordBotSettingsResponse)
async def get_discord_bot(
    db: DatabaseSession, current_user: ActiveUser
) -> DiscordBotSettingsResponse:
    return _bot_response(await db.get(UserDiscordBot, current_user.id))


@router.put("/api/auth/discord-bot", response_model=DiscordBotSettingsResponse)
async def update_discord_bot(
    request: DiscordBotSettingsUpdate,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> DiscordBotSettingsResponse:
    bot = await db.get(UserDiscordBot, current_user.id)
    if bot is None:
        bot = UserDiscordBot(user_id=current_user.id)
    if request.token is not None:
        try:
            identity = await test_bot_token(request.token)
        except DiscordBotAPIError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        duplicate = await db.execute(
            select(UserDiscordBot).where(
                UserDiscordBot.user_id != current_user.id,
                or_(
                    UserDiscordBot.application_id == identity.application_id,
                    UserDiscordBot.bot_user_id == identity.bot_user_id,
                ),
            )
        )
        if duplicate.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This Discord Bot is already configured by another panel user",
            )
        bot.token_encrypted = encrypt_credential(request.token)
        bot.application_id = identity.application_id
        bot.bot_user_id = identity.bot_user_id
        bot.username = identity.username
        bot.discriminator = identity.discriminator
        bot.last_error = None
        bot.connection_status = "configured"
    if request.enabled is not None:
        if request.enabled and not bot.token_encrypted:
            raise HTTPException(status_code=409, detail="Configure a Bot Token before enabling")
        bot.enabled = request.enabled
        if not request.enabled:
            bot.connection_status = "disabled"
    if request.message_trigger_mode is not None:
        bot.message_trigger_mode = request.message_trigger_mode
    db.add(bot)
    await db.commit()
    await db.refresh(bot)
    await _notify_manager(current_user.id)
    return _bot_response(bot)


@router.delete("/api/auth/discord-bot", response_model=DiscordBotSettingsResponse)
async def delete_discord_bot(
    db: DatabaseSession, current_user: ActiveUser
) -> DiscordBotSettingsResponse:
    bot = await db.get(UserDiscordBot, current_user.id)
    if bot is None:
        return _bot_response(None)
    bot.token_encrypted = None
    bot.enabled = False
    bot.application_id = None
    bot.bot_user_id = None
    bot.connection_status = "not_configured"
    bot.last_error = None
    result = await db.execute(
        select(ServerDiscordBinding).where(ServerDiscordBinding.user_id == current_user.id)
    )
    for binding in result.scalars().all():
        binding.invalid_reason = "bot_token_missing"
        db.add(binding)
    db.add(bot)
    await db.commit()
    await _notify_manager(current_user.id)
    return _bot_response(bot)


@router.post("/api/auth/discord-bot/test", response_model=DiscordBotTestResponse)
async def test_discord_bot(
    request: DiscordBotTestRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> DiscordBotTestResponse:
    token = request.token
    if token is None:
        _bot, token = await _stored_token(db, current_user.id)
    try:
        identity = await test_bot_token(token)
    except DiscordBotAPIError as exc:
        return DiscordBotTestResponse(success=False, message=str(exc))
    return DiscordBotTestResponse(
        success=True,
        application_id=identity.application_id,
        bot_user_id=identity.bot_user_id,
        username=identity.username,
        message="Discord Bot Token is valid",
    )


@router.get("/api/auth/discord-bot/guilds", response_model=list[DiscordGuildOption])
async def get_discord_bot_guilds(
    db: DatabaseSession, current_user: ActiveUser
) -> list[DiscordGuildOption]:
    _bot, token = await _stored_token(db, current_user.id)
    try:
        guilds, _channels, _roles = await _load_discord_options(current_user.id, token)
    except DiscordBotAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [
        DiscordGuildOption(
            id=str(item["id"]), name=str(item.get("name") or item["id"]), icon=item.get("icon")
        )
        for item in guilds
    ]


@router.get(
    "/api/auth/discord-bot/global-options",
    response_model=DiscordBotOptionsResponse,
)
async def get_discord_global_binding_options(
    db: DatabaseSession,
    current_user: ActiveUser,
    guild_id: str | None = Query(default=None, pattern=r"^[1-9][0-9]{0,19}$"),
) -> DiscordBotOptionsResponse:
    _bot, token = await _stored_token(db, current_user.id)
    return await _discord_options_response(current_user.id, token, guild_id)


@router.get(
    "/api/auth/discord-bot/global-settings",
    response_model=DiscordGlobalBindingResponse,
)
async def get_discord_global_binding(
    db: DatabaseSession, current_user: ActiveUser
) -> DiscordGlobalBindingResponse:
    bot = await db.get(UserDiscordBot, current_user.id)
    if bot is None:
        bot = UserDiscordBot(user_id=current_user.id)
    return await _global_binding_response(db, bot)


@router.put(
    "/api/auth/discord-bot/global-settings",
    response_model=DiscordGlobalBindingResponse,
)
async def update_discord_global_binding(
    request: DiscordGlobalBindingUpdate,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> DiscordGlobalBindingResponse:
    bot, token = await _stored_token(db, current_user.id)
    await _validate_binding_selection(
        current_user.id,
        token,
        guild_id=request.guild_id,
        channel_ids=list(request.channel_ids),
        role_ids=list(request.role_ids),
    )
    bot.global_binding_configured = True
    bot.global_binding_enabled = request.enabled
    bot.global_guild_id = request.guild_id
    bot.global_channel_ids = list(request.channel_ids)
    bot.global_role_ids = list(request.role_ids)
    bot.global_user_ids = list(request.user_ids)
    bot.global_allow_channel_managers = request.allow_channel_managers
    bot.global_capabilities = [item.value for item in request.capabilities]
    db.add(bot)
    synced_server_count = 0
    if request.sync_existing_servers:
        synced_server_count = await sync_global_discord_binding(db, bot)
    await db.commit()
    if synced_server_count:
        await _notify_manager(current_user.id)
    return await _global_binding_response(db, bot, synced_server_count=synced_server_count)


@router.get(
    "/api/auth/discord-bot/menu-options",
    response_model=DiscordMenuPushOptionsResponse,
)
async def get_discord_menu_push_options(
    db: DatabaseSession,
    current_user: ActiveUser,
    guild_id: str | None = Query(default=None, pattern=r"^[1-9][0-9]{0,19}$"),
) -> DiscordMenuPushOptionsResponse:
    _bot, token = await _connected_bot_token(db, current_user.id)
    allowed = await _bound_menu_push_channels(db, current_user.id)
    try:
        guild_payload, channels, _roles = await _load_discord_options(
            current_user.id, token, guild_id
        )
    except DiscordBotAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    guilds = [
        DiscordGuildOption(
            id=str(item["id"]), name=str(item.get("name") or item["id"]), icon=item.get("icon")
        )
        for item in guild_payload
        if str(item["id"]) in allowed
    ]
    if guild_id is None:
        return DiscordMenuPushOptionsResponse(guilds=guilds)
    if guild_id not in allowed:
        raise HTTPException(
            status_code=422,
            detail="The selected Guild has no enabled Discord server binding",
        )
    allowed_channel_ids = allowed[guild_id]
    return DiscordMenuPushOptionsResponse(
        guilds=guilds,
        channels=[
            DiscordChannelOption(
                id=str(item["id"]),
                guild_id=guild_id,
                name=str(item.get("name") or item["id"]),
                type=int(item.get("type", 0)),
            )
            for item in channels
            if str(item["id"]) in allowed_channel_ids
            and int(item.get("type", -1)) in DISCORD_MENU_PUSH_CHANNEL_TYPES
        ],
    )


@router.post("/api/auth/discord-bot/menu", response_model=DiscordMenuPushResponse)
async def push_discord_menu(
    request: DiscordMenuPushRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> DiscordMenuPushResponse:
    _bot, token = await _connected_bot_token(db, current_user.id)
    allowed = await _bound_menu_push_channels(db, current_user.id)
    if request.channel_id not in allowed.get(request.guild_id, set()):
        raise HTTPException(
            status_code=422,
            detail="The selected channel is not part of an enabled Discord server binding",
        )
    try:
        _guilds, channels, _roles = await _load_discord_options(
            current_user.id, token, request.guild_id
        )
    except DiscordBotAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    channel = next((item for item in channels if str(item.get("id")) == request.channel_id), None)
    if channel is None or int(channel.get("type", -1)) not in DISCORD_MENU_PUSH_CHANNEL_TYPES:
        raise HTTPException(
            status_code=422,
            detail="The selected channel cannot receive a proactive menu message",
        )
    allowed_request, retry_after = await redis_manager.hit_rate_limit(
        f"discord_menu_push:{current_user.id}:{request.guild_id}:{request.channel_id}", 1, 5
    )
    if not allowed_request:
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {retry_after} seconds before pushing another menu",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        locale = await get_guild_locale(token, request.guild_id)
        message_id, _issued_at = await send_menu_launcher(token, request.channel_id, locale)
    except DiscordBotAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    discord_menu_task_registry.create(
        delete_menu_launcher_after(token, request.channel_id, message_id)
    )
    return DiscordMenuPushResponse(
        guild_id=request.guild_id,
        channel_id=request.channel_id,
        message_id=message_id,
    )


async def _owned_server(db: DatabaseSession, current_user: ActiveUser, server_id: int):
    server = await require_server_access(db, server_id, current_user)
    if server.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Only the server owner may configure its Discord Bot binding",
        )
    return server


async def _binding_response(
    db: DatabaseSession, server_id: int, user_id: int
) -> DiscordBindingResponse:
    binding = await db.get(ServerDiscordBinding, server_id)
    bot = await db.get(UserDiscordBot, user_id)
    disabled_reason = None
    if binding is None or not binding.enabled:
        disabled_reason = "binding_disabled"
    elif bot is None or not bot.token_encrypted:
        disabled_reason = "bot_token_missing"
    elif not bot.enabled:
        disabled_reason = "bot_disabled"
    elif bot.connection_status != "connected":
        disabled_reason = "bot_not_connected"
    elif binding.invalid_reason:
        disabled_reason = binding.invalid_reason
    return DiscordBindingResponse(
        server_id=server_id,
        enabled=bool(binding and binding.enabled),
        effective_enabled=disabled_reason is None,
        disabled_reason=disabled_reason,
        guild_id=binding.guild_id if binding else None,
        channel_ids=list(binding.channel_ids or []) if binding else [],
        role_ids=list(binding.role_ids or []) if binding else [],
        user_ids=list(binding.user_ids or []) if binding else [],
        allow_channel_managers=binding.allow_channel_managers if binding else False,
        capabilities=list(binding.capabilities or []) if binding else [],
        response_visibility="public",
    )


@router.get("/servers/{server_id}/discord-bot-settings", response_model=DiscordBindingResponse)
async def get_server_discord_bot_settings(
    server_id: int, db: DatabaseSession, current_user: ActiveUser
) -> DiscordBindingResponse:
    server = await _owned_server(db, current_user, server_id)
    return await _binding_response(db, server.id, server.user_id)


@router.put("/servers/{server_id}/discord-bot-settings", response_model=DiscordBindingResponse)
async def update_server_discord_bot_settings(
    server_id: int,
    request: DiscordBindingUpdate,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> DiscordBindingResponse:
    server = await _owned_server(db, current_user, server_id)
    _bot, token = await _stored_token(db, server.user_id)
    await _validate_binding_selection(
        server.user_id,
        token,
        guild_id=request.guild_id,
        channel_ids=list(request.channel_ids),
        role_ids=list(request.role_ids),
    )
    binding = await db.get(ServerDiscordBinding, server.id)
    if binding is None:
        binding = ServerDiscordBinding(server_id=server.id, user_id=server.user_id)
    binding.enabled = request.enabled
    binding.guild_id = request.guild_id
    binding.channel_ids = list(request.channel_ids)
    binding.role_ids = list(request.role_ids)
    binding.user_ids = list(request.user_ids)
    binding.allow_channel_managers = request.allow_channel_managers
    binding.capabilities = [item.value for item in request.capabilities]
    binding.response_visibility = "public"
    binding.invalid_reason = None
    db.add(binding)
    await db.commit()
    await _notify_manager(server.user_id)
    return await _binding_response(db, server.id, server.user_id)


@router.get("/servers/{server_id}/discord-bot-options", response_model=DiscordBotOptionsResponse)
async def get_server_discord_bot_options(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
    guild_id: str | None = Query(default=None, pattern=r"^[1-9][0-9]{0,19}$"),
) -> DiscordBotOptionsResponse:
    server = await _owned_server(db, current_user, server_id)
    _bot, token = await _stored_token(db, server.user_id)
    return await _discord_options_response(server.user_id, token, guild_id)


async def _agent_policy_response(db: DatabaseSession, server_id: int, owner) -> AgentPolicyResponse:
    policy = await get_effective_agent_policy(db, server_id)
    disabled_reason = None
    if not policy.enabled:
        disabled_reason = "policy_disabled"
    elif await get_effective_provider(db, owner) is None:
        disabled_reason = "provider_unavailable"
    return AgentPolicyResponse(
        server_id=server_id,
        enabled=policy.enabled,
        effective_enabled=disabled_reason is None,
        disabled_reason=disabled_reason,
        capabilities=list(sorted(policy.capabilities, key=lambda item: item.value)),
    )


@router.get("/servers/{server_id}/agent-policy", response_model=AgentPolicyResponse)
async def get_server_agent_policy(
    server_id: int, db: DatabaseSession, current_user: ActiveUser
) -> AgentPolicyResponse:
    server = await require_server_access(db, server_id, current_user)
    owner = (
        current_user if server.user_id == current_user.id else await db.get(User, server.user_id)
    )
    if owner is None:
        raise HTTPException(status_code=404, detail="Server owner not found")
    return await _agent_policy_response(db, server.id, owner)


@router.put("/servers/{server_id}/agent-policy", response_model=AgentPolicyResponse)
async def update_server_agent_policy(
    server_id: int,
    request: AgentPolicyUpdate,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> AgentPolicyResponse:
    server = await require_server_access(db, server_id, current_user)
    policy = await db.get(ServerAgentPolicy, server.id)
    if policy is None:
        policy = ServerAgentPolicy(server_id=server.id)
    policy.enabled = request.enabled
    policy.capabilities = [item.value for item in request.capabilities]
    db.add(policy)
    await db.commit()
    owner = (
        current_user if server.user_id == current_user.id else await db.get(User, server.user_id)
    )
    if owner is None:
        raise HTTPException(status_code=404, detail="Server owner not found")
    return await _agent_policy_response(db, server.id, owner)
