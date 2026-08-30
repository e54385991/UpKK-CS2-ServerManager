"""Versioned Discord bot, global binding, and menu-push settings."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from api.dependencies import ActiveUser, DatabaseSession
from api.routes import discord_bot as legacy
from modules import DiscordBotSettingsUpdate, DiscordBotTestRequest
from modules.schemas.discord import (
    DiscordCapability,
    DiscordGlobalBindingUpdate,
    DiscordMenuPushRequest,
)

from .schemas import (
    ActionResult,
    DiscordBindingUpdateRequest,
    DiscordBotTestBody,
    DiscordBotTestView,
    DiscordBotUpdateRequest,
    DiscordBotView,
    DiscordChannelView,
    DiscordGlobalBindingView,
    DiscordGuildView,
    DiscordMenuPushBody,
    DiscordMenuPushView,
    DiscordOptionsView,
    DiscordRoleView,
)

router = APIRouter(prefix="/api/v1/discord", tags=["v1-discord"])


def _bot_view(payload) -> DiscordBotView:
    return DiscordBotView(
        enabled=bool(payload.enabled),
        token_configured=bool(payload.token_configured),
        message_trigger_mode=payload.message_trigger_mode,
        username=payload.username,
        connection_status=str(payload.connection_status),
        last_error=payload.last_error,
        invite_url=payload.invite_url,
    )


def _guilds(payload) -> list[DiscordGuildView]:
    return [
        DiscordGuildView(id=str(item.id), name=str(item.name), icon=item.icon) for item in payload
    ]


def _options_view(
    payload, *, token_configured: bool, message: str | None = None
) -> DiscordOptionsView:
    return DiscordOptionsView(
        token_configured=token_configured,
        guilds=_guilds(getattr(payload, "guilds", []) or []),
        channels=[
            DiscordChannelView(
                id=str(item.id),
                guild_id=str(item.guild_id),
                name=str(item.name),
                type=int(getattr(item, "type", 0) or 0),
            )
            for item in getattr(payload, "channels", []) or []
        ],
        roles=[
            DiscordRoleView(
                id=str(item.id),
                guild_id=str(item.guild_id),
                name=str(item.name),
                position=int(getattr(item, "position", 0) or 0),
            )
            for item in getattr(payload, "roles", []) or []
        ],
        message=message,
    )


def _global_view(payload) -> DiscordGlobalBindingView:
    return DiscordGlobalBindingView(
        configured=bool(payload.configured),
        enabled=bool(payload.enabled),
        guild_id=payload.guild_id,
        channel_ids=list(payload.channel_ids or []),
        role_ids=list(payload.role_ids or []),
        user_ids=list(payload.user_ids or []),
        allow_channel_managers=bool(payload.allow_channel_managers),
        allow_server_administrators=bool(payload.allow_server_administrators),
        capabilities=[str(item) for item in (payload.capabilities or [])],
        server_count=int(payload.server_count or 0),
        matching_server_count=int(payload.matching_server_count or 0),
        synced_server_count=int(payload.synced_server_count or 0),
        inherited_by_new_servers=bool(payload.inherited_by_new_servers),
    )


def _binding_update(body: DiscordBindingUpdateRequest) -> DiscordGlobalBindingUpdate:
    return DiscordGlobalBindingUpdate(
        enabled=body.enabled,
        guild_id=body.guild_id,
        channel_ids=list(body.channel_ids),
        role_ids=list(body.role_ids),
        user_ids=list(body.user_ids),
        allow_channel_managers=body.allow_channel_managers,
        allow_server_administrators=body.allow_server_administrators,
        capabilities=[DiscordCapability(item) for item in body.capabilities],
        sync_existing_servers=body.sync_existing_servers,
    )


@router.get("", response_model=DiscordBotView)
async def get_discord_bot(db: DatabaseSession, current_user: ActiveUser) -> DiscordBotView:
    return _bot_view(await legacy.get_discord_bot(db, current_user))


@router.put("", response_model=DiscordBotView)
async def update_discord_bot(
    body: DiscordBotUpdateRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
) -> DiscordBotView:
    payload = await legacy.update_discord_bot(
        DiscordBotSettingsUpdate(
            token=body.token,
            enabled=body.enabled,
            message_trigger_mode=body.message_trigger_mode,
        ),
        db,
        current_user,
        request,
    )
    return _bot_view(payload)


@router.delete("", response_model=ActionResult)
async def delete_discord_bot(
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
) -> ActionResult:
    await legacy.delete_discord_bot(db, current_user, request)
    return ActionResult(success=True, message="Discord bot removed")


@router.post("/test", response_model=DiscordBotTestView)
async def test_discord_bot(
    body: DiscordBotTestBody,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> DiscordBotTestView:
    payload = await legacy.test_discord_bot(
        DiscordBotTestRequest(token=body.token),
        db,
        current_user,
    )
    return DiscordBotTestView(
        success=bool(payload.success),
        username=payload.username,
        message=str(payload.message),
    )


@router.get("/guilds", response_model=DiscordOptionsView)
async def get_discord_guilds(db: DatabaseSession, current_user: ActiveUser) -> DiscordOptionsView:
    try:
        guilds = await legacy.get_discord_bot_guilds(db, current_user)
    except HTTPException as exc:
        if exc.status_code == 409:
            return DiscordOptionsView(token_configured=False, message=str(exc.detail))
        raise
    return DiscordOptionsView(token_configured=True, guilds=_guilds(guilds))


@router.get("/global", response_model=DiscordGlobalBindingView)
async def get_discord_global_binding(
    db: DatabaseSession, current_user: ActiveUser
) -> DiscordGlobalBindingView:
    return _global_view(await legacy.get_discord_global_binding(db, current_user))


@router.put("/global", response_model=DiscordGlobalBindingView)
async def update_discord_global_binding(
    body: DiscordBindingUpdateRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
) -> DiscordGlobalBindingView:
    payload = await legacy.update_discord_global_binding(
        _binding_update(body),
        db,
        current_user,
        request,
    )
    return _global_view(payload)


@router.get("/global/options", response_model=DiscordOptionsView)
async def get_discord_global_options(
    db: DatabaseSession,
    current_user: ActiveUser,
    guild_id: str | None = Query(default=None, pattern=r"^[1-9][0-9]{0,19}$"),
) -> DiscordOptionsView:
    try:
        payload = await legacy.get_discord_global_binding_options(db, current_user, guild_id)
    except HTTPException as exc:
        if exc.status_code == 409:
            return DiscordOptionsView(token_configured=False, message=str(exc.detail))
        raise
    return _options_view(payload, token_configured=True)


@router.get("/menu/options", response_model=DiscordOptionsView)
async def get_discord_menu_options(
    db: DatabaseSession,
    current_user: ActiveUser,
    guild_id: str | None = Query(default=None, pattern=r"^[1-9][0-9]{0,19}$"),
) -> DiscordOptionsView:
    try:
        payload = await legacy.get_discord_menu_push_options(db, current_user, guild_id)
    except HTTPException as exc:
        if exc.status_code in {400, 409}:
            return DiscordOptionsView(
                token_configured=exc.status_code != 409,
                message=str(exc.detail),
            )
        raise
    return _options_view(payload, token_configured=True)


@router.post("/menu", response_model=DiscordMenuPushView)
async def push_discord_menu(
    body: DiscordMenuPushBody,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> DiscordMenuPushView:
    payload = await legacy.push_discord_menu(
        DiscordMenuPushRequest(guild_id=body.guild_id, channel_id=body.channel_id),
        db,
        current_user,
    )
    return DiscordMenuPushView(
        guild_id=str(payload.guild_id),
        channel_id=str(payload.channel_id),
        message_id=str(payload.message_id),
        expires_in_seconds=int(payload.expires_in_seconds),
    )
