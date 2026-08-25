"""Discord Gateway Bot and server-level AI authorization regressions."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from api.routes.discord_bot import _bot_response
from modules.models import (
    AuthType,
    DiscordOperationRun,
    Server,
    ServerDiscordBinding,
    User,
    UserDiscordBot,
)
from modules.schemas.discord import (
    DEFAULT_AGENT_CAPABILITIES,
    AgentCapability,
    DiscordBindingUpdate,
    DiscordBotSettingsResponse,
)
from modules.utils import get_current_time
from services.agent_policy_service import get_effective_agent_policy
from services.ai_tools import TOOLS_BY_NAME, tool_definitions
from services.discord_authorization_service import authorized_bindings
from services.discord_bot_manager import ManagedDiscordClient, discord_bot_manager
from services.discord_bot_service import MINIMUM_BOT_PERMISSIONS, build_invite_url
from services.discord_operation_service import (
    DiscordOperationDenied,
    canonical_payload,
    confirm_operation,
)


def test_snowflakes_remain_strings_and_unknown_capabilities_fail_closed():
    request = DiscordBindingUpdate.model_validate(
        {
            "enabled": True,
            "guild_id": "123456789012345678",
            "channel_ids": ["223456789012345678"],
            "role_ids": ["323456789012345678"],
            "capabilities": ["status"],
        }
    )
    assert isinstance(request.guild_id, str)
    assert request.channel_ids == ["223456789012345678"]

    with pytest.raises(ValidationError):
        DiscordBindingUpdate.model_validate(
            {
                "enabled": True,
                "guild_id": "123",
                "channel_ids": ["456"],
                "role_ids": ["789"],
                "capabilities": ["arbitrary_shell"],
            }
        )
    with pytest.raises(ValidationError):
        DiscordBindingUpdate.model_validate(
            {
                "enabled": True,
                "guild_id": "123",
                "channel_ids": [],
                "role_ids": [],
            }
        )


@pytest.mark.asyncio
async def test_missing_agent_policy_uses_only_three_read_capabilities():
    db = AsyncMock()
    db.get.return_value = None
    policy = await get_effective_agent_policy(db, 7)
    assert policy.enabled is True
    assert policy.persisted is False
    assert policy.capabilities == frozenset(DEFAULT_AGENT_CAPABILITIES)


def test_tool_visibility_and_parameter_resolvers_follow_capabilities():
    readonly = frozenset(DEFAULT_AGENT_CAPABILITIES)
    names = {
        item["function"]["name"]
        for item in tool_definitions(server_selected=True, allowed_capabilities=readonly)
    }
    assert "inspect_server" in names
    assert "read_server_text_file" in names
    assert "plan_plugin_install" in names
    assert "control_server" not in names
    assert "execute_saved_host_command" not in names

    control = TOOLS_BY_NAME["control_server"]
    assert control.required_capabilities({"action": "start"}) == frozenset({AgentCapability.START})
    assert control.required_capabilities({"action": "stop"}) == frozenset({AgentCapability.STOP})
    operation = TOOLS_BY_NAME["run_server_operation"]
    assert operation.required_capabilities({"operation": "install_metamod"}) == frozenset(
        {AgentCapability.MANAGE_FRAMEWORKS}
    )


@pytest.mark.asyncio
async def test_whitelist_has_no_discord_administrator_bypass():
    bot = UserDiscordBot(user_id=1, token_encrypted="encrypted", enabled=True)
    owner = User(
        id=1,
        username="owner",
        email="owner@example.com",
        hashed_password="hash",
        is_active=True,
    )
    server = Server(
        id=10,
        user_id=1,
        name="Server",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
    )
    binding = ServerDiscordBinding(
        server_id=10,
        user_id=1,
        enabled=True,
        guild_id="100",
        channel_ids=["200"],
        role_ids=["300"],
        user_ids=["400"],
        capabilities=["status"],
    )
    db = AsyncMock()
    db.get.side_effect = [bot, owner]
    result = AsyncMock()
    result.all = lambda: [(binding, server)]
    db.execute.return_value = result

    # Administrator is deliberately irrelevant: without an explicit ID or role
    # match, authorization fails.
    denied = await authorized_bindings(
        db,
        bot_owner_user_id=1,
        guild_id="100",
        channel_id="200",
        actor_user_id="999",
        actor_role_ids={"999999"},
    )
    assert denied == []


def test_bot_token_is_not_part_of_any_response_contract():
    bot = UserDiscordBot(
        user_id=1,
        enabled=True,
        token_encrypted="secret-ciphertext",
        application_id="123",
        bot_user_id="456",
    )
    response = _bot_response(bot)
    payload = DiscordBotSettingsResponse.model_validate(response).model_dump()
    assert payload["token_configured"] is True
    assert "token" not in payload
    assert "token_encrypted" not in payload
    assert "secret-ciphertext" not in str(payload)


def test_gateway_uses_only_guild_intent_and_has_expected_command_tree():
    client = ManagedDiscordClient(discord_bot_manager, 1)
    assert client.intents.guilds is True
    assert client.intents.message_content is False
    assert client.intents.members is False
    cs2 = client.tree.get_command("cs2")
    assert cs2 is not None
    assert {command.name for command in cs2.commands} == {
        "help",
        "status",
        "start",
        "stop",
        "restart",
        "update",
        "validate",
        "plugin",
        "agent",
    }


def test_invite_and_confirmation_payloads_are_minimal_and_stable():
    invite = build_invite_url("123")
    assert invite is not None
    assert "scope=bot+applications.commands" in invite
    assert f"permissions={MINIMUM_BOT_PERMISSIONS}" in invite
    assert "administrator" not in invite.casefold()

    first, first_hash = canonical_payload({"server": 1, "action": "restart"})
    second, second_hash = canonical_payload({"action": "restart", "server": 1})
    assert first == second
    assert first_hash == second_hash


@pytest.mark.asyncio
async def test_confirmation_rejects_expiry_repeat_click_and_non_requester():
    _arguments, arguments_hash = canonical_payload({"action": "restart"})
    _plan, plan_hash = canonical_payload({"server_id": 10, "action": "restart"})

    async def run(item: DiscordOperationRun, actor: str = "400"):
        db = AsyncMock()
        db.add = Mock()
        selected = AsyncMock()
        selected.scalar_one_or_none = lambda: item
        db.execute.return_value = selected
        return db, await confirm_operation(
            db,
            operation_id=item.id,
            actor_user_id=actor,
            actor_role_ids={"300"},
            fresh_plan={"server_id": 10, "action": "restart"},
        )

    repeated = DiscordOperationRun(
        id="00000000-0000-0000-0000-000000000001",
        server_id=10,
        owner_user_id=1,
        actor_user_id="400",
        guild_id="100",
        channel_id="200",
        action="restart",
        required_capabilities=["restart"],
        arguments={"action": "restart"},
        arguments_hash=arguments_hash,
        plan_snapshot={"server_id": 10, "action": "restart"},
        plan_hash=plan_hash,
        status="queued",
        expires_at=get_current_time() + timedelta(minutes=10),
    )
    with pytest.raises(DiscordOperationDenied, match="no longer pending"):
        await run(repeated)

    expired = repeated.model_copy(
        update={
            "id": "00000000-0000-0000-0000-000000000002",
            "status": "pending",
            "expires_at": get_current_time() - timedelta(seconds=1),
        }
    )
    with pytest.raises(DiscordOperationDenied, match="expired"):
        await run(expired)
    assert expired.status == "expired"

    other_actor = repeated.model_copy(
        update={"id": "00000000-0000-0000-0000-000000000003", "status": "pending"}
    )
    with pytest.raises(DiscordOperationDenied, match="original requester"):
        await run(other_actor, actor="999")


@pytest.mark.asyncio
async def test_confirmation_rechecks_plan_binding_capability_and_ownership(monkeypatch):
    arguments = {"plugin_id": 5}
    _arguments, arguments_hash = canonical_payload(arguments)
    original_plan = {"plugin_id": 5, "version": "1.0"}
    _plan, plan_hash = canonical_payload(original_plan)
    item = DiscordOperationRun(
        id="00000000-0000-0000-0000-000000000004",
        server_id=10,
        owner_user_id=1,
        actor_user_id="400",
        guild_id="100",
        channel_id="200",
        action="plugin_upgrade",
        required_capabilities=["plugin_upgrade"],
        arguments=arguments,
        arguments_hash=arguments_hash,
        plan_snapshot=original_plan,
        plan_hash=plan_hash,
        status="pending",
        expires_at=get_current_time() + timedelta(minutes=10),
    )
    server = Server(
        id=10,
        user_id=1,
        name="Server",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
    )
    owner = User(
        id=1,
        username="owner",
        email="owner@example.com",
        hashed_password="hash",
        is_active=True,
    )
    binding = ServerDiscordBinding(
        server_id=10,
        user_id=1,
        enabled=True,
        guild_id="100",
        channel_ids=["200"],
        role_ids=["300"],
        capabilities=["plugin_upgrade"],
    )
    db = AsyncMock()
    db.add = Mock()
    selected = AsyncMock()
    selected.scalar_one_or_none = lambda: item
    db.execute.return_value = selected
    db.get.side_effect = [server, owner]
    monkeypatch.setattr(
        "services.discord_operation_service.authorized_bindings",
        AsyncMock(return_value=[(binding, server)]),
    )

    with pytest.raises(DiscordOperationDenied, match="plan changed"):
        await confirm_operation(
            db,
            operation_id=item.id,
            actor_user_id="400",
            actor_role_ids={"300"},
            fresh_plan={"plugin_id": 5, "version": "2.0"},
        )
