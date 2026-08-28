"""Discord-isolated AI conversations and initiator-bound tool approvals."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from sqlalchemy import func
from sqlmodel import select

from modules.database import async_session_maker
from modules.models import AIConversation, AIMessage, AIRun, AIToolRun, Server, User
from modules.schemas.discord import DiscordCapability
from modules.utils import get_current_time
from services.agent_policy_service import get_effective_agent_policy, require_agent_capabilities
from services.ai_orchestrator import ACTIVE_RUN_STATUSES, process_ai_run
from services.ai_security import AIConfigurationError, get_effective_provider, redact_sensitive_text
from services.ai_tools import TOOLS_BY_NAME, canonical_arguments
from services.discord_authorization_service import authorized_bindings


class DiscordAIError(ValueError):
    pass


_SERVER_REFERENCE_PARTS = re.compile(r"[a-z]+[0-9]*|[0-9]+|[\u3400-\u9fff]+")
_GENERIC_SERVER_REFERENCES = frozenset(
    {"cs", "cs2", "server", "servers", "upkk", "服务器", "伺服器", "服"}
)


def _reference_phrases(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    parts = _SERVER_REFERENCE_PARTS.findall(normalized)
    phrases = set(parts)
    phrases.update(
        match.group(1)
        for part in parts
        if (match := re.fullmatch(r"([a-z]+)[0-9]+", part)) is not None
    )
    for width in range(2, min(3, len(parts)) + 1):
        phrases.update(
            "".join(parts[start : start + width]) for start in range(len(parts) - width + 1)
        )
    return {
        item
        for item in phrases
        if len(item) >= 2 and not item.isdecimal() and item not in _GENERIC_SERVER_REFERENCES
    }


def resolve_discord_agent_server(prompt: str, servers: Iterable[Server]) -> Server | None:
    """Resolve one explicitly referenced authorized server without asking the model to enumerate."""

    candidates = [server for server in servers if server.id is not None]
    if len(candidates) == 1:
        return candidates[0]
    prompt_phrases = _reference_phrases(prompt)
    scored = [
        (
            max(
                (len(alias) for alias in _reference_phrases(server.name) & prompt_phrases),
                default=0,
            ),
            server,
        )
        for server in candidates
    ]
    best_score = max((score for score, _server in scored), default=0)
    matches = [server for score, server in scored if score == best_score and score > 0]
    return matches[0] if len(matches) == 1 else None


async def available_discord_agent_server_ids(
    *, owner_user_id: int, server_ids: Iterable[int]
) -> frozenset[int]:
    """Return owner-scoped servers with an enabled Agent and tested AI provider."""

    requested_ids = {int(server_id) for server_id in server_ids}
    if not requested_ids:
        return frozenset()
    async with async_session_maker() as db:
        owner = await db.get(User, owner_user_id)
        if owner is None or not owner.is_active:
            return frozenset()
        try:
            provider = await get_effective_provider(db, owner)
        except AIConfigurationError:
            return frozenset()
        if provider is None:
            return frozenset()
        result = await db.execute(
            select(Server.id).where(
                Server.id.in_(requested_ids),
                Server.user_id == owner_user_id,
            )
        )
        owned_ids = {int(server_id) for server_id in result.scalars().all()}
        available = {
            server_id
            for server_id in sorted(owned_ids)
            if (await get_effective_agent_policy(db, server_id)).enabled
        }
    return frozenset(available)


async def reset_discord_conversation(
    *, owner_user_id: int, server_id: int, actor_user_id: str, guild_id: str, channel_id: str
) -> str:
    async with async_session_maker() as db:
        conversation = AIConversation(
            user_id=owner_user_id,
            server_id=server_id,
            title="Discord conversation",
            source="discord",
            external_actor_id=actor_user_id,
            discord_guild_id=guild_id,
            discord_channel_id=channel_id,
        )
        db.add(conversation)
        await db.commit()
        return conversation.id


async def _latest_conversation(
    db, *, owner_user_id: int, server_id: int, actor_user_id: str, guild_id: str, channel_id: str
) -> AIConversation | None:
    result = await db.execute(
        select(AIConversation)
        .where(
            AIConversation.user_id == owner_user_id,
            AIConversation.server_id == server_id,
            AIConversation.source == "discord",
            AIConversation.external_actor_id == actor_user_id,
            AIConversation.discord_guild_id == guild_id,
            AIConversation.discord_channel_id == channel_id,
        )
        .order_by(AIConversation.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def ask_discord_agent(
    *,
    owner_user_id: int,
    server_id: int,
    actor_user_id: str,
    guild_id: str,
    channel_id: str,
    prompt: str,
) -> str:
    async with async_session_maker() as db:
        owner = await db.get(User, owner_user_id)
        server = await db.get(Server, server_id)
        if owner is None or not owner.is_active or server is None or server.user_id != owner.id:
            raise DiscordAIError("Server owner is unavailable")
        await require_agent_capabilities(db, server_id, frozenset())
        if await get_effective_provider(db, owner) is None:
            raise DiscordAIError("No tested AI provider is enabled for the server owner")
        conversation = await _latest_conversation(
            db,
            owner_user_id=owner_user_id,
            server_id=server_id,
            actor_user_id=actor_user_id,
            guild_id=guild_id,
            channel_id=channel_id,
        )
        if conversation is None:
            conversation = AIConversation(
                user_id=owner_user_id,
                server_id=server_id,
                title=prompt[:80],
                source="discord",
                external_actor_id=actor_user_id,
                discord_guild_id=guild_id,
                discord_channel_id=channel_id,
            )
            db.add(conversation)
            await db.flush()
        active = await db.execute(
            select(func.count())
            .select_from(AIRun)
            .where(
                AIRun.conversation_id == conversation.id,
                AIRun.status.in_(ACTIVE_RUN_STATUSES),
            )
        )
        if int(active.scalar_one()):
            raise DiscordAIError("This Discord AI conversation already has an active run")
        db.add(
            AIMessage(
                conversation_id=conversation.id,
                role="user",
                content=prompt.strip(),
                visible=True,
            )
        )
        run = AIRun(
            conversation_id=conversation.id,
            user_id=owner.id,
            server_id=server.id,
            status="queued",
            source="discord",
            external_actor_id=actor_user_id,
        )
        conversation.updated_at = get_current_time()
        db.add(run)
        db.add(conversation)
        await db.commit()
        run_id = run.id
    await process_ai_run(run_id)
    return run_id


async def discord_run_snapshot(run_id: str) -> dict:
    async with async_session_maker() as db:
        run = await db.get(AIRun, run_id)
        if run is None or run.source != "discord":
            raise DiscordAIError("Discord AI run not found")
        message_result = await db.execute(
            select(AIMessage)
            .where(
                AIMessage.conversation_id == run.conversation_id,
                AIMessage.role == "assistant",
                AIMessage.visible.is_(True),
            )
            .order_by(AIMessage.id.desc())
            .limit(1)
        )
        tool_result = await db.execute(
            select(AIToolRun)
            .where(AIToolRun.run_id == run.id, AIToolRun.status == "pending_approval")
            .order_by(AIToolRun.created_at.asc())
            .limit(1)
        )
        message = message_result.scalar_one_or_none()
        tool = tool_result.scalar_one_or_none()
        progress_result = await db.execute(
            select(AIToolRun)
            .where(AIToolRun.run_id == run.id)
            .order_by(AIToolRun.created_at.desc())
            .limit(1)
        )
        progress_tool = progress_result.scalar_one_or_none()
        return {
            "run_id": run.id,
            "status": run.status,
            "error": run.error,
            "message": redact_sensitive_text(message.content, limit=3900) if message else None,
            "progress": (
                {
                    "tool": progress_tool.tool_name,
                    "status": progress_tool.status,
                    "snapshot": progress_tool.progress_snapshot,
                }
                if progress_tool
                else None
            ),
            "tool": (
                {
                    "id": tool.id,
                    "name": tool.tool_name,
                    "arguments_hash": tool.arguments_hash,
                    "plan": tool.plan_snapshot,
                    "expires_at": tool.approval_expires_at,
                }
                if tool
                else None
            ),
        }


async def approve_discord_tool(
    *,
    run_id: str,
    tool_run_id: str,
    actor_user_id: str,
    actor_role_ids: set[str],
    actor_is_channel_manager: bool = False,
    actor_is_server_administrator: bool = False,
    guild_id: str,
    channel_id: str,
) -> None:
    async with async_session_maker() as db:
        run = await db.get(AIRun, run_id)
        tool = await db.get(AIToolRun, tool_run_id)
        if (
            run is None
            or tool is None
            or tool.run_id != run.id
            or run.source != "discord"
            or tool.status != "pending_approval"
        ):
            raise DiscordAIError("AI approval is no longer pending")
        conversation = await db.get(AIConversation, run.conversation_id)
        if (
            conversation is None
            or run.external_actor_id != actor_user_id
            or conversation.external_actor_id != actor_user_id
            or conversation.discord_guild_id != guild_id
            or conversation.discord_channel_id != channel_id
        ):
            raise DiscordAIError("Only the original Discord requester may approve this tool")
        now = get_current_time()
        expires = tool.approval_expires_at
        if expires is None:
            raise DiscordAIError("AI approval has no expiry")
        if expires.tzinfo is None:
            now = now.replace(tzinfo=None)
        if expires <= now:
            raise DiscordAIError("AI approval expired; ask for a fresh plan")
        _serialized, current_hash = canonical_arguments(tool.arguments)
        if current_hash != tool.arguments_hash:
            raise DiscordAIError("AI tool arguments changed after planning")
        if run.server_id is None:
            raise DiscordAIError("AI write tool has no selected server")
        bindings = await authorized_bindings(
            db,
            bot_owner_user_id=run.user_id,
            guild_id=guild_id,
            channel_id=channel_id,
            actor_user_id=actor_user_id,
            actor_role_ids=actor_role_ids,
            actor_is_channel_manager=actor_is_channel_manager,
            actor_is_server_administrator=actor_is_server_administrator,
            required_capability=DiscordCapability.AGENT_ASK,
        )
        if run.server_id not in {server.id for _binding, server in bindings}:
            raise DiscordAIError("Discord Agent authorization was revoked")
        spec = TOOLS_BY_NAME.get(tool.tool_name)
        if spec is None:
            raise DiscordAIError("AI tool is no longer available")
        await require_agent_capabilities(
            db, run.server_id, spec.required_capabilities(tool.arguments)
        )
        tool.status = "queued"
        tool.approved_by = run.user_id
        tool.approved_at = get_current_time()
        tool.approved_actor_type = "discord"
        tool.approved_external_actor_id = actor_user_id
        db.add(tool)
        await db.commit()
    await process_ai_run(run_id)
