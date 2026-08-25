"""Server-wide AI capability evaluation shared by Web and Discord."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from modules.models import ServerAgentPolicy
from modules.schemas.discord import DEFAULT_AGENT_CAPABILITIES, AgentCapability


class AgentCapabilityDenied(PermissionError):
    """Raised when a server AI policy does not authorize an operation."""


@dataclass(frozen=True, slots=True)
class EffectiveAgentPolicy:
    enabled: bool
    capabilities: frozenset[AgentCapability]
    persisted: bool


async def get_effective_agent_policy(db: AsyncSession, server_id: int) -> EffectiveAgentPolicy:
    policy = await db.get(ServerAgentPolicy, server_id)
    if policy is None:
        return EffectiveAgentPolicy(True, frozenset(DEFAULT_AGENT_CAPABILITIES), False)
    try:
        capabilities = frozenset(AgentCapability(value) for value in policy.capabilities)
    except ValueError:
        # Unknown persisted values must never broaden access.
        capabilities = frozenset(
            AgentCapability(value)
            for value in policy.capabilities
            if value in AgentCapability._value2member_map_
        )
    return EffectiveAgentPolicy(bool(policy.enabled), capabilities, True)


async def require_agent_capabilities(
    db: AsyncSession,
    server_id: int,
    required: set[AgentCapability] | frozenset[AgentCapability],
) -> EffectiveAgentPolicy:
    policy = await get_effective_agent_policy(db, server_id)
    if not policy.enabled:
        raise AgentCapabilityDenied("AI Agent is disabled for this server")
    missing = set(required) - set(policy.capabilities)
    if missing:
        names = ", ".join(sorted(item.value for item in missing))
        raise AgentCapabilityDenied(f"AI capability is disabled: {names}")
    return policy
