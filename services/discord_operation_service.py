"""Persistent Discord operation confirmations with idempotent state transitions."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from modules.models import DiscordOperationRun, Server, User
from modules.schemas.discord import DiscordCapability
from modules.utils import get_current_time
from services.discord_authorization_service import authorized_bindings

CONFIRMATION_TTL_MINUTES = 15


class DiscordOperationDenied(PermissionError):
    pass


def canonical_payload(payload: dict) -> tuple[dict, str]:
    clean = json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    serialized = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return clean, hashlib.sha256(serialized.encode()).hexdigest()


async def create_operation(
    db: AsyncSession,
    *,
    server: Server,
    actor_user_id: str,
    actor_role_ids: set[str],
    actor_is_channel_manager: bool = False,
    guild_id: str,
    channel_id: str,
    action: str,
    required_capabilities: list[DiscordCapability],
    arguments: dict,
    plan: dict,
) -> DiscordOperationRun:
    fresh_server = await db.get(Server, server.id)
    owner = await db.get(User, server.user_id)
    if (
        fresh_server is None
        or owner is None
        or not owner.is_active
        or fresh_server.user_id != owner.id
    ):
        raise DiscordOperationDenied("Server ownership is no longer valid")
    bindings = await authorized_bindings(
        db,
        bot_owner_user_id=owner.id,
        guild_id=guild_id,
        channel_id=channel_id,
        actor_user_id=actor_user_id,
        actor_role_ids=actor_role_ids,
        actor_is_channel_manager=actor_is_channel_manager,
    )
    binding = next(
        (binding for binding, bound_server in bindings if bound_server.id == fresh_server.id), None
    )
    allowed = set(binding.capabilities) if binding is not None else set()
    if binding is None or any(
        capability.value not in allowed for capability in required_capabilities
    ):
        raise DiscordOperationDenied("Discord authorization was revoked")

    clean_arguments, arguments_hash = canonical_payload(arguments)
    clean_plan, plan_hash = canonical_payload(plan)
    item = DiscordOperationRun(
        server_id=fresh_server.id,
        owner_user_id=fresh_server.user_id,
        actor_user_id=actor_user_id,
        guild_id=guild_id,
        channel_id=channel_id,
        action=action,
        required_capabilities=[item.value for item in required_capabilities],
        arguments=clean_arguments,
        arguments_hash=arguments_hash,
        plan_snapshot=clean_plan,
        plan_hash=plan_hash,
        expires_at=get_current_time() + timedelta(minutes=CONFIRMATION_TTL_MINUTES),
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def confirm_operation(
    db: AsyncSession,
    *,
    operation_id: str,
    actor_user_id: str,
    actor_role_ids: set[str],
    actor_is_channel_manager: bool = False,
    fresh_plan: dict,
) -> DiscordOperationRun:
    result = await db.execute(
        select(DiscordOperationRun).where(DiscordOperationRun.id == operation_id).with_for_update()
    )
    item = result.scalar_one_or_none()
    if item is None or item.status != "pending":
        raise DiscordOperationDenied("Operation is no longer pending")
    now = get_current_time()
    expires_at = item.expires_at
    if expires_at.tzinfo is None:
        now = now.replace(tzinfo=None)
    if expires_at <= now:
        item.status = "expired"
        db.add(item)
        await db.commit()
        raise DiscordOperationDenied("Confirmation expired; request a new plan")
    if item.actor_user_id != actor_user_id:
        raise DiscordOperationDenied("Only the original requester may confirm this operation")
    _clean_arguments, current_arguments_hash = canonical_payload(item.arguments)
    if current_arguments_hash != item.arguments_hash:
        raise DiscordOperationDenied("Operation arguments changed after planning")
    server = await db.get(Server, item.server_id)
    owner = await db.get(User, item.owner_user_id)
    if server is None or owner is None or not owner.is_active or server.user_id != owner.id:
        raise DiscordOperationDenied("Server ownership is no longer valid")
    required = [DiscordCapability(value) for value in item.required_capabilities]
    bindings = await authorized_bindings(
        db,
        bot_owner_user_id=owner.id,
        guild_id=item.guild_id,
        channel_id=item.channel_id,
        actor_user_id=actor_user_id,
        actor_role_ids=actor_role_ids,
        actor_is_channel_manager=actor_is_channel_manager,
    )
    binding = next(
        (binding for binding, bound_server in bindings if bound_server.id == server.id), None
    )
    if binding is None or any(cap.value not in set(binding.capabilities) for cap in required):
        raise DiscordOperationDenied("Discord authorization was revoked")
    _, fresh_plan_hash = canonical_payload(fresh_plan)
    if fresh_plan_hash != item.plan_hash:
        raise DiscordOperationDenied("Operation plan changed; request a new confirmation")
    item.status = "queued"
    item.confirmed_at = get_current_time()
    db.add(item)
    await db.commit()
    return item
