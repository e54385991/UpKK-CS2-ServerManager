"""Per-user Discord Bot and server-scoped Discord/AI permission APIs."""

from __future__ import annotations

from typing import Literal as Literal

from fastapi import APIRouter as APIRouter
from fastapi import HTTPException as HTTPException
from fastapi import Query as Query
from fastapi import Request as Request
from fastapi import status as status
from sqlalchemy import or_ as or_
from sqlmodel import col as col
from sqlmodel import select as select

from api.dependencies import ActiveUser as ActiveUser
from api.dependencies import DatabaseSession as DatabaseSession
from api.dependencies import require_server_access as require_server_access
from modules import AgentPolicyResponse as AgentPolicyResponse
from modules import AgentPolicyUpdate as AgentPolicyUpdate
from modules import DiscordBindingResponse as DiscordBindingResponse
from modules import DiscordBindingUpdate as DiscordBindingUpdate
from modules import DiscordBotOptionsResponse as DiscordBotOptionsResponse
from modules import DiscordBotSettingsResponse as DiscordBotSettingsResponse
from modules import DiscordBotSettingsUpdate as DiscordBotSettingsUpdate
from modules import DiscordBotTestRequest as DiscordBotTestRequest
from modules import DiscordBotTestResponse as DiscordBotTestResponse
from modules import DiscordCapability as DiscordCapability
from modules import DiscordChannelOption as DiscordChannelOption
from modules import DiscordGlobalBindingResponse as DiscordGlobalBindingResponse
from modules import DiscordGlobalBindingUpdate as DiscordGlobalBindingUpdate
from modules import DiscordGuildOption as DiscordGuildOption
from modules import DiscordMenuPushOptionsResponse as DiscordMenuPushOptionsResponse
from modules import DiscordMenuPushRequest as DiscordMenuPushRequest
from modules import DiscordMenuPushResponse as DiscordMenuPushResponse
from modules import DiscordRoleOption as DiscordRoleOption
from modules import Server as Server
from modules import ServerAgentPolicy as ServerAgentPolicy
from modules import ServerDiscordBinding as ServerDiscordBinding
from modules import User as User
from modules import UserDiscordBot as UserDiscordBot
from services.agent_policy_service import get_effective_agent_policy as get_effective_agent_policy
from services.ai_security import AIConfigurationError as AIConfigurationError
from services.ai_security import decrypt_credential as decrypt_credential
from services.ai_security import encrypt_credential as encrypt_credential
from services.ai_security import get_effective_provider as get_effective_provider
from services.audit_log_service import record_audit_event as record_audit_event
from services.discord_binding_template_service import global_binding_counts as global_binding_counts
from services.discord_binding_template_service import (
    sync_global_discord_binding as sync_global_discord_binding,
)
from services.discord_bot_service import (
    DISCORD_COMMAND_CHANNEL_TYPES as DISCORD_COMMAND_CHANNEL_TYPES,
)
from services.discord_bot_service import (
    DISCORD_MENU_PUSH_CHANNEL_TYPES as DISCORD_MENU_PUSH_CHANNEL_TYPES,
)
from services.discord_bot_service import DiscordBotAPIError as DiscordBotAPIError
from services.discord_bot_service import build_invite_url as build_invite_url
from services.discord_bot_service import delete_menu_launcher_after as delete_menu_launcher_after
from services.discord_bot_service import get_guild_locale as get_guild_locale
from services.discord_bot_service import get_guild_options as get_guild_options
from services.discord_bot_service import list_guilds as list_guilds
from services.discord_bot_service import send_menu_launcher as send_menu_launcher
from services.discord_bot_service import test_bot_token as test_bot_token
from services.redis_manager import redis_manager as redis_manager
from services.task_registry import discord_menu_task_registry as discord_menu_task_registry

router = APIRouter(tags=["discord-bot"])


def _discord_capability(value: str) -> DiscordCapability | None:
    """Convert persisted strings to the strict response enum."""
    try:
        return DiscordCapability(value)
    except ValueError:
        return None


def _trigger_mode(value: str | None) -> Literal["mention_only", "mention_and_greetings"]:
    return "mention_and_greetings" if value == "mention_and_greetings" else "mention_only"


async def _notify_manager(user_id: int) -> None:
    from services.discord_bot_manager import discord_bot_manager

    await discord_bot_manager.reconcile_user(user_id)


from .discord_bot_settings import _bot_response as _bot_response
from .discord_bot_settings import _bound_menu_push_channels as _bound_menu_push_channels
from .discord_bot_settings import _connected_bot_token as _connected_bot_token
from .discord_bot_settings import _discord_options_response as _discord_options_response
from .discord_bot_settings import _global_binding_response as _global_binding_response
from .discord_bot_settings import _load_discord_options as _load_discord_options  # noqa: E402
from .discord_bot_settings import _stored_token as _stored_token
from .discord_bot_settings import _validate_binding_selection as _validate_binding_selection
from .discord_bot_settings import delete_discord_bot as delete_discord_bot
from .discord_bot_settings import get_discord_bot as get_discord_bot
from .discord_bot_settings import test_discord_bot as test_discord_bot
from .discord_bot_settings import update_discord_bot as update_discord_bot


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
    http_request: Request,
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
    bot.global_allow_server_administrators = request.allow_server_administrators
    bot.global_capabilities = [item.value for item in request.capabilities]
    db.add(bot)
    synced_server_count = 0
    if request.sync_existing_servers:
        synced_server_count = await sync_global_discord_binding(db, bot)
    await db.commit()
    if synced_server_count:
        await _notify_manager(current_user.id)
    await record_audit_event(
        category="settings",
        action="discord.global_binding.update",
        status="success",
        user=current_user,
        request=http_request,
        details={
            "enabled": request.enabled,
            "synced_server_count": synced_server_count,
            "guild_id": request.guild_id,
        },
    )
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
    _bot, token = await _stored_token(db, current_user.id)
    allowed = await _bound_menu_push_channels(db, current_user.id)
    try:
        guild_payload, channels, _roles = await _load_discord_options(
            current_user.id, token, guild_id
        )
    except DiscordBotAPIError as exc:
        if allowed:
            return DiscordMenuPushOptionsResponse(
                guilds=[
                    DiscordGuildOption(id=item_id, name=item_id) for item_id in sorted(allowed)
                ],
                channels=[],
            )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    guilds = [
        DiscordGuildOption(
            id=str(item["id"]), name=str(item.get("name") or item["id"]), icon=item.get("icon")
        )
        for item in guild_payload
    ]
    if guild_id is None:
        return DiscordMenuPushOptionsResponse(guilds=guilds)
    allowed_channel_ids = allowed.get(guild_id, set())
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
        allow_server_administrators=binding.allow_server_administrators if binding else False,
        capabilities=(
            [
                capability
                for value in binding.capabilities or []
                if (capability := _discord_capability(value)) is not None
            ]
            if binding
            else []
        ),
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
    http_request: Request,
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
    binding.allow_server_administrators = request.allow_server_administrators
    binding.capabilities = [item.value for item in request.capabilities]
    binding.response_visibility = "public"
    binding.invalid_reason = None
    db.add(binding)
    await db.commit()
    await _notify_manager(server.user_id)
    await record_audit_event(
        category="settings",
        action="discord.binding.update",
        status="success",
        user=current_user,
        request=http_request,
        server_id=server_id,
        details={"enabled": request.enabled, "guild_id": request.guild_id},
    )
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


async def _usable_provider(db: DatabaseSession, owner) -> bool:
    """Whether the agent has a provider it can actually call.

    A stored API key becomes undecryptable whenever AI_CREDENTIAL_ENCRYPTION_KEY
    changes after it was saved. That is an unusable provider, not a broken
    request: reading the policy must keep working so the page can explain it.
    """
    try:
        return await get_effective_provider(db, owner) is not None
    except AIConfigurationError:
        return False


async def _agent_policy_response(db: DatabaseSession, server_id: int, owner) -> AgentPolicyResponse:
    policy = await get_effective_agent_policy(db, server_id)
    disabled_reason = None
    if not policy.enabled:
        disabled_reason = "policy_disabled"
    elif not await _usable_provider(db, owner):
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
    http_request: Request,
) -> AgentPolicyResponse:
    server = await require_server_access(db, server_id, current_user)
    policy = await db.get(ServerAgentPolicy, server.id)
    if policy is None:
        policy = ServerAgentPolicy(server_id=server.id)
    policy.enabled = request.enabled
    policy.capabilities = [item.value for item in request.capabilities]
    db.add(policy)
    await db.commit()
    await record_audit_event(
        category="settings",
        action="discord.agent_policy.update",
        status="success",
        user=current_user,
        request=http_request,
        server_id=server_id,
        details={
            "enabled": request.enabled,
            "capabilities": [item.value for item in request.capabilities],
        },
    )
    owner = (
        current_user if server.user_id == current_user.id else await db.get(User, server.user_id)
    )
    if owner is None:
        raise HTTPException(status_code=404, detail="Server owner not found")
    return await _agent_policy_response(db, server.id, owner)
