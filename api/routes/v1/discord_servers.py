"""Per-server Discord binding and AI agent policy for the Next console."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from api.dependencies import ActiveUser, DatabaseSession
from api.routes import discord_bot as legacy
from modules.schemas.discord import (
    AgentCapability,
    AgentPolicyUpdate,
    DiscordBindingUpdate,
    DiscordCapability,
)

from .discord import _options_view
from .schemas import (
    AgentPolicyUpdateRequest,
    AgentPolicyView,
    DiscordBindingUpdateRequest,
    DiscordBindingView,
    DiscordOptionsView,
)

router = APIRouter(prefix="/api/v1/servers", tags=["v1-discord"])


def _binding_view(payload) -> DiscordBindingView:
    return DiscordBindingView(
        server_id=int(payload.server_id),
        enabled=bool(payload.enabled),
        effective_enabled=bool(payload.effective_enabled),
        disabled_reason=payload.disabled_reason,
        guild_id=payload.guild_id,
        channel_ids=list(payload.channel_ids or []),
        role_ids=list(payload.role_ids or []),
        user_ids=list(payload.user_ids or []),
        allow_channel_managers=bool(payload.allow_channel_managers),
        allow_server_administrators=bool(payload.allow_server_administrators),
        capabilities=[str(item) for item in (payload.capabilities or [])],
        response_visibility="public",
    )


def _policy_view(payload) -> AgentPolicyView:
    return AgentPolicyView(
        server_id=int(payload.server_id),
        enabled=bool(payload.enabled),
        effective_enabled=bool(payload.effective_enabled),
        disabled_reason=payload.disabled_reason,
        capabilities=[str(item) for item in (payload.capabilities or [])],
    )


def _binding_update(body: DiscordBindingUpdateRequest) -> DiscordBindingUpdate:
    return DiscordBindingUpdate(
        enabled=body.enabled,
        guild_id=body.guild_id,
        channel_ids=list(body.channel_ids),
        role_ids=list(body.role_ids),
        user_ids=list(body.user_ids),
        allow_channel_managers=body.allow_channel_managers,
        allow_server_administrators=body.allow_server_administrators,
        capabilities=[DiscordCapability(item) for item in body.capabilities],
    )


@router.get("/{server_id}/discord", response_model=DiscordBindingView)
async def get_server_discord_binding(
    server_id: int, db: DatabaseSession, current_user: ActiveUser
) -> DiscordBindingView:
    return _binding_view(await legacy.get_server_discord_bot_settings(server_id, db, current_user))


@router.put("/{server_id}/discord", response_model=DiscordBindingView)
async def update_server_discord_binding(
    server_id: int,
    body: DiscordBindingUpdateRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
) -> DiscordBindingView:
    return _binding_view(
        await legacy.update_server_discord_bot_settings(
            server_id,
            _binding_update(body),
            db,
            current_user,
            request,
        )
    )


@router.get("/{server_id}/discord/options", response_model=DiscordOptionsView)
async def get_server_discord_options(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
    guild_id: str | None = Query(default=None, pattern=r"^[1-9][0-9]{0,19}$"),
) -> DiscordOptionsView:
    try:
        payload = await legacy.get_server_discord_bot_options(server_id, db, current_user, guild_id)
    except HTTPException as exc:
        if exc.status_code == 409:
            return DiscordOptionsView(token_configured=False, message=str(exc.detail))
        raise
    return _options_view(payload, token_configured=True)


@router.get("/{server_id}/agent-policy", response_model=AgentPolicyView)
async def get_server_agent_policy(
    server_id: int, db: DatabaseSession, current_user: ActiveUser
) -> AgentPolicyView:
    return _policy_view(await legacy.get_server_agent_policy(server_id, db, current_user))


@router.put("/{server_id}/agent-policy", response_model=AgentPolicyView)
async def update_server_agent_policy(
    server_id: int,
    body: AgentPolicyUpdateRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
) -> AgentPolicyView:
    return _policy_view(
        await legacy.update_server_agent_policy(
            server_id,
            AgentPolicyUpdate(
                enabled=body.enabled,
                capabilities=[AgentCapability(item) for item in body.capabilities],
            ),
            db,
            current_user,
            request,
        )
    )
