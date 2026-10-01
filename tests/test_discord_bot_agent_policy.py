"""Discord Gateway Bot and server-level AI authorization regressions."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from pydantic import ValidationError

from api.routes.discord_bot import (
    _bot_response,
)
from modules.models import (
    AuthType,
    Server,
    ServerDiscordBinding,
    ServerStatus,
    User,
    UserDiscordBot,
)
from modules.schemas.discord import (
    DEFAULT_AGENT_CAPABILITIES,
    AgentCapability,
    DiscordBindingUpdate,
    DiscordBotSettingsResponse,
    DiscordBotSettingsUpdate,
    DiscordCapability,
    DiscordGlobalBindingUpdate,
    DiscordMenuPushRequest,
)
from modules.utils import get_current_time
from services.a2s_cache_service import a2s_cache_service
from services.a2s_query import a2s_service
from services.agent_policy_service import get_effective_agent_policy
from services.ai_security import redact_sensitive_text
from services.ai_tools import TOOLS_BY_NAME, GameConsoleCommandInput, tool_definitions
from services.discord_authorization_service import authorized_bindings
from services.discord_bot_manager import (
    DiscordBotManager,
    ManagedDiscordClient,
    _is_channel_manager,
    _is_server_administrator,
    discord_bot_manager,
    format_panel_update_age,
    load_panel_status_sources,
    status_card_fields,
)
from services.disk_space_service import disk_space_service

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from tests.discord_bot_agent_policy_fakes import (  # noqa: E402 - preserves initialization and registration order
    _auth_fixtures as _auth_fixtures,
)
from tests.discord_bot_agent_policy_fakes import (  # noqa: E402 - preserves initialization and registration order
    _discord_http_error as _discord_http_error,
)
from tests.discord_bot_agent_policy_fakes import (  # noqa: E402 - preserves initialization and registration order
    _global_bot as _global_bot,
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
    managers_only = DiscordBindingUpdate.model_validate(
        {
            "enabled": True,
            "guild_id": "123456789012345678",
            "channel_ids": ["223456789012345678"],
            "allow_channel_managers": True,
        }
    )
    assert managers_only.allow_channel_managers is True
    assert managers_only.allow_server_administrators is False
    assert managers_only.role_ids == []
    assert managers_only.user_ids == []
    admins_only = DiscordBindingUpdate.model_validate(
        {
            "enabled": True,
            "guild_id": "123456789012345678",
            "channel_ids": ["223456789012345678"],
            "allow_server_administrators": True,
        }
    )
    assert admins_only.allow_server_administrators is True
    assert admins_only.allow_channel_managers is False
    with pytest.raises(ValidationError):
        DiscordBindingUpdate.model_validate(
            {
                "enabled": True,
                "guild_id": "123456789012345678",
                "channel_ids": ["223456789012345678"],
            }
        )
    push = DiscordMenuPushRequest(guild_id="123456789012345678", channel_id="223456789012345678")
    assert isinstance(push.guild_id, str)
    with pytest.raises(ValidationError):
        DiscordMenuPushRequest(guild_id="guild", channel_id="223456789012345678")


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
    assert "read_game_console" in names
    assert "search_map_pool" in names
    assert "plan_plugin_install" in names
    assert "control_server" not in names
    assert "change_current_map" not in names
    assert "execute_saved_host_command" not in names

    control = TOOLS_BY_NAME["control_server"]
    assert control.required_capabilities({"action": "start"}) == frozenset({AgentCapability.START})
    assert control.required_capabilities({"action": "stop"}) == frozenset({AgentCapability.STOP})
    operation = TOOLS_BY_NAME["run_server_operation"]
    assert operation.required_capabilities({"operation": "install_metamod"}) == frozenset(
        {AgentCapability.MANAGE_FRAMEWORKS}
    )
    game_console = TOOLS_BY_NAME["send_game_console_command"]
    assert game_console.required_capabilities({"command": "status"}) == frozenset(
        {AgentCapability.SEND_GAME_CONSOLE_COMMANDS}
    )
    assert game_console.is_exposed(readonly) is False
    assert TOOLS_BY_NAME["read_game_console"].required_capabilities({"lines": 120}) == frozenset(
        {AgentCapability.READ_LOGS_FILES}
    )
    change_map = TOOLS_BY_NAME["change_current_map"]
    assert change_map.required_capabilities({"query": "saw"}) == frozenset()
    assert change_map.is_exposed(readonly) is False
    assert change_map.is_exposed(frozenset({AgentCapability.CHANGE_CURRENT_MAP})) is True
    assert change_map.is_exposed(frozenset({AgentCapability.SEND_GAME_CONSOLE_COMMANDS})) is True
    assert TOOLS_BY_NAME["search_map_pool"].is_exposed(frozenset({AgentCapability.INSPECT_STATUS}))


def test_game_console_input_is_single_command_and_redacts_console_secrets():
    assert GameConsoleCommandInput(command="  status  ").command == "status"
    with pytest.raises(ValidationError, match="Only one game console command"):
        GameConsoleCommandInput(command="status\nquit")
    assert redact_sensitive_text('sv_password "do-not-show"') == "sv_password [REDACTED]"


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
        actor_is_channel_manager=True,
    )
    assert denied == []


@pytest.mark.asyncio
async def test_channel_managers_are_authorized_only_when_explicitly_enabled():
    db, binding, server = _auth_fixtures(allow_channel_managers=True)
    allowed = await authorized_bindings(
        db,
        bot_owner_user_id=1,
        guild_id="100",
        channel_id="200",
        actor_user_id="999",
        actor_role_ids=set(),
        actor_is_channel_manager=True,
    )
    assert [(item[0], item[1]) for item in allowed] == [(binding, server)]

    db, _binding, _server = _auth_fixtures(allow_channel_managers=True)
    denied_without_permission = await authorized_bindings(
        db,
        bot_owner_user_id=1,
        guild_id="100",
        channel_id="200",
        actor_user_id="999",
        actor_role_ids=set(),
        actor_is_channel_manager=False,
    )
    assert denied_without_permission == []

    db, _binding, _server = _auth_fixtures(allow_channel_managers=False)
    denied_when_disabled = await authorized_bindings(
        db,
        bot_owner_user_id=1,
        guild_id="100",
        channel_id="200",
        actor_user_id="999",
        actor_role_ids=set(),
        actor_is_channel_manager=True,
    )
    assert denied_when_disabled == []


@pytest.mark.asyncio
async def test_server_administrators_are_authorized_only_when_explicitly_enabled():
    db, binding, server = _auth_fixtures(allow_server_administrators=True)
    allowed = await authorized_bindings(
        db,
        bot_owner_user_id=1,
        guild_id="100",
        channel_id="200",
        actor_user_id="999",
        actor_role_ids=set(),
        actor_is_server_administrator=True,
    )
    assert [(item[0], item[1]) for item in allowed] == [(binding, server)]

    db, _binding, _server = _auth_fixtures(allow_server_administrators=True)
    denied_without_permission = await authorized_bindings(
        db,
        bot_owner_user_id=1,
        guild_id="100",
        channel_id="200",
        actor_user_id="999",
        actor_role_ids=set(),
        actor_is_server_administrator=False,
        actor_is_channel_manager=True,
    )
    assert denied_without_permission == []

    db, _binding, _server = _auth_fixtures(allow_server_administrators=False)
    denied_when_disabled = await authorized_bindings(
        db,
        bot_owner_user_id=1,
        guild_id="100",
        channel_id="200",
        actor_user_id="999",
        actor_role_ids=set(),
        actor_is_server_administrator=True,
    )
    assert denied_when_disabled == []


@pytest.mark.asyncio
async def test_authorized_bindings_fall_back_to_global_template_for_unbound_servers():
    bot = UserDiscordBot(
        user_id=1,
        token_encrypted="encrypted",
        enabled=True,
        global_binding_configured=True,
        global_binding_enabled=True,
        global_guild_id="100",
        global_channel_ids=["200"],
        global_user_ids=["400"],
        global_capabilities=["status"],
    )
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
    binding_result = Mock()
    binding_result.all.return_value = []
    server_result = Mock()
    server_result.scalars.return_value.all.return_value = [server]
    db = AsyncMock()
    db.get.side_effect = [bot, owner]
    db.execute.side_effect = [binding_result, server_result]

    allowed = await authorized_bindings(
        db,
        bot_owner_user_id=1,
        guild_id="100",
        channel_id="200",
        actor_user_id="400",
        actor_role_ids=set(),
    )
    assert len(allowed) == 1
    assert allowed[0][1] is server
    assert allowed[0][0].channel_ids == ["200"]
    assert allowed[0][0].capabilities == ["status"]

    db.get.side_effect = [bot, owner]
    db.execute.side_effect = [binding_result, server_result]
    denied = await authorized_bindings(
        db,
        bot_owner_user_id=1,
        guild_id="100",
        channel_id="200",
        actor_user_id="999",
        actor_role_ids=set(),
    )
    assert denied == []

    existing = ServerDiscordBinding(
        server_id=10,
        user_id=1,
        enabled=True,
        guild_id="100",
        channel_ids=["200"],
        user_ids=["999"],
        capabilities=["status"],
    )
    configured = Mock()
    configured.all.return_value = [(existing, server)]
    owned = Mock()
    owned.scalars.return_value.all.return_value = [server]
    db.get.side_effect = [bot, owner]
    db.execute.side_effect = [configured, owned]
    skipped = await authorized_bindings(
        db,
        bot_owner_user_id=1,
        guild_id="100",
        channel_id="200",
        actor_user_id="400",
        actor_role_ids=set(),
    )
    assert skipped == []


def test_channel_manager_detection_uses_manage_channels_or_administrator():
    assert (
        _is_channel_manager(
            SimpleNamespace(permissions=SimpleNamespace(manage_channels=True, administrator=False))
        )
        is True
    )
    assert (
        _is_channel_manager(
            SimpleNamespace(permissions=SimpleNamespace(manage_channels=False, administrator=True))
        )
        is True
    )
    assert (
        _is_channel_manager(
            SimpleNamespace(
                permissions=SimpleNamespace(manage_channels=False, administrator=False),
                channel=None,
                user=None,
            )
        )
        is False
    )
    channel = SimpleNamespace(
        permissions_for=lambda _member: SimpleNamespace(manage_channels=True, administrator=False)
    )
    assert (
        _is_channel_manager(
            SimpleNamespace(
                permissions=None,
                channel=channel,
                author=SimpleNamespace(id=9),
            )
        )
        is True
    )


def test_server_administrator_detection_uses_administrator_or_guild_owner():
    assert (
        _is_server_administrator(
            SimpleNamespace(permissions=SimpleNamespace(manage_channels=False, administrator=True))
        )
        is True
    )
    assert (
        _is_server_administrator(
            SimpleNamespace(permissions=SimpleNamespace(manage_channels=True, administrator=False))
        )
        is False
    )
    assert (
        _is_server_administrator(
            SimpleNamespace(
                permissions=SimpleNamespace(manage_channels=False, administrator=False),
                guild=SimpleNamespace(owner_id=9),
                user=SimpleNamespace(id=9),
            )
        )
        is True
    )
    assert (
        _is_server_administrator(
            SimpleNamespace(
                permissions=SimpleNamespace(manage_channels=False, administrator=False),
                guild=SimpleNamespace(owner_id=1),
                user=SimpleNamespace(id=9),
                channel=None,
            )
        )
        is False
    )


def test_status_card_matches_panel_overview_and_does_not_invent_missing_values():
    server = Server(
        id=10,
        user_id=1,
        name="CS2-ZE",
        host="fulldown.upkk.com",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        game_port=27015,
        server_name="ZE Panel Name",
        default_map="de_dust2",
        max_players=64,
        current_game_version="1.40.9.4",
        status=ServerStatus.RUNNING,
    )
    last_updated = (get_current_time() - timedelta(seconds=2)).isoformat()
    online = dict(
        status_card_fields(
            server,
            a2s_ok=True,
            info={
                "server_name": "[UPKK]CS2 KZ #1",
                "version": "1.41.7.7",
                "map_name": "kz_justin",
                "player_count": 0,
                "max_players": 13,
                "ping": 0.069,
            },
            locale="zh-CN",
            response_time_ms=69,
            last_updated=last_updated,
            disk_info={"used_gb": 129.93, "total_gb": 489.0, "used_percent": 26.57},
        )
    )
    assert online["在线状态"] == "服务器在线"
    assert online["服务器名称"] == "[UPKK]CS2 KZ #1"
    assert online["地图"] == "kz_justin"
    assert online["玩家"] == "0/13"
    assert online["延迟"] == "69ms"
    assert online["版本"] == "1.41.7.7"
    assert online["更新时间"] == format_panel_update_age(last_updated)
    assert online["目录占用"] == "129.93 GB"
    assert online["磁盘总量"] == "489.00 GB"
    assert online["占用率"] == "26.57%"
    assert "ZE Panel Name" not in online.values()
    assert "de_dust2" not in online.values()
    assert "1.40.9.4" not in online.values()

    offline = dict(status_card_fields(server, a2s_ok=False, info=None, locale="en-US"))
    assert offline["Availability"] == "Server offline or query failed"
    assert offline["Version"] == "unknown"
    assert offline["Map"] == "unknown"
    assert offline["Players"] == "unknown"
    assert offline["Ping"] == "unknown"
    assert offline["Directory"] == "unknown"
    assert offline["Updated"] == "unknown"
    assert "de_dust2" not in offline.values()
    assert "1.40.9.4" not in offline.values()


def test_panel_update_age_matches_overview_relative_format():
    now = get_current_time()
    assert format_panel_update_age((now - timedelta(seconds=2)).isoformat()) == "2s ago"
    assert format_panel_update_age((now - timedelta(minutes=3)).isoformat()) == "3m ago"
    assert format_panel_update_age("not-a-timestamp") is None
    assert format_panel_update_age(None) is None


@pytest.mark.asyncio
async def test_status_sources_reuse_panel_a2s_and_disk_caches(monkeypatch):
    server = Server(
        id=10,
        user_id=1,
        name="CS2-ZE",
        host="fulldown.upkk.com",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        game_port=27015,
    )
    cached = {
        "success": True,
        "server_info": {"server_name": "[UPKK]CS2 KZ #1", "map_name": "kz_justin"},
        "response_time_ms": 69,
        "last_updated": "2026-08-26T00:00:00+08:00",
    }
    live_query = AsyncMock(side_effect=AssertionError("live A2S must not run when cache exists"))

    async def cached_info(_server_id):
        return cached

    async def disk_space(_server, cache_only=False):
        assert cache_only is True
        return True, {"used_gb": 129.93, "total_gb": 489.0, "used_percent": 26.57}

    monkeypatch.setattr(a2s_cache_service, "get_cached_info", cached_info)
    monkeypatch.setattr(a2s_service, "query_server_info", live_query)
    monkeypatch.setattr(disk_space_service, "get_disk_space", disk_space)

    sources = await load_panel_status_sources(server)
    assert sources["a2s_ok"] is True
    assert sources["info"]["server_name"] == "[UPKK]CS2 KZ #1"
    assert sources["response_time_ms"] == 69
    assert sources["disk_info"]["used_percent"] == 26.57
    live_query.assert_not_called()


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
    assert payload["message_trigger_mode"] == "mention_only"
    assert "token" not in payload
    assert "token_encrypted" not in payload
    assert "secret-ciphertext" not in str(payload)


def test_gateway_uses_guild_messages_and_conditionally_privileged_content_intent():
    client = ManagedDiscordClient(discord_bot_manager, 1)
    assert client.intents.guilds is True
    assert client.intents.guild_messages is True
    assert client.intents.message_content is False
    assert client.intents.members is False
    greeting_client = ManagedDiscordClient(discord_bot_manager, 1, "mention_and_greetings")
    assert greeting_client.intents.guild_messages is True
    assert greeting_client.intents.message_content is True
    cs2 = client.tree.get_command("cs2")
    assert cs2 is not None
    assert {command.name for command in cs2.commands} == {
        "help",
        "menu",
        "status",
        "start",
        "stop",
        "restart",
        "update",
        "validate",
        "map",
        "plugin",
        "console",
        "agent",
    }
    console = next(command for command in cs2.commands if command.name == "console")
    assert {command.name for command in console.commands} == {"send"}


def test_message_trigger_mode_is_strict_and_defaults_to_mention_only():
    bot = UserDiscordBot(user_id=1)
    assert bot.message_trigger_mode == "mention_only"
    assert bot.global_binding_configured is False
    assert bot.global_channel_ids == []
    assert DiscordBotSettingsUpdate().message_trigger_mode is None
    assert (
        DiscordBotSettingsUpdate(message_trigger_mode="mention_and_greetings").message_trigger_mode
        == "mention_and_greetings"
    )
    with pytest.raises(ValidationError):
        DiscordBotSettingsUpdate(message_trigger_mode="read_everything")


def test_global_binding_contract_reuses_strict_single_server_rules():
    request = DiscordGlobalBindingUpdate(
        enabled=True,
        guild_id="100",
        channel_ids=["200"],
        role_ids=["300"],
        capabilities=[DiscordCapability.STATUS, DiscordCapability.RESTART],
        sync_existing_servers=True,
    )
    assert request.sync_existing_servers is True
    assert request.capabilities == [DiscordCapability.STATUS, DiscordCapability.RESTART]
    with pytest.raises(ValidationError):
        DiscordGlobalBindingUpdate(
            enabled=True,
            guild_id="100",
            channel_ids=[],
            role_ids=[],
        )


def test_profile_guide_exposes_trigger_mode_and_channel_manager_controls():
    form = (PROJECT_ROOT / "frontend/src/modules/discord/discord-form.tsx").read_text(
        encoding="utf-8"
    )
    binding = (PROJECT_ROOT / "frontend/src/modules/discord/discord-binding-form.tsx").read_text(
        encoding="utf-8"
    )
    assert "messageTriggerMode" in form
    assert "mention_only" in form
    assert "allowChannelManagers" in binding
    assert "allowServerAdministrators" in binding
    assert "syncExistingServers" in binding
    for locale in ("en-US", "zh-CN"):
        messages = json.loads(
            (PROJECT_ROOT / f"frontend/src/i18n/messages/{locale}.json").read_text(encoding="utf-8")
        )["discord"]
        assert messages["mentionOnly"]
        assert messages["allowManagers"]
        assert messages["allowAdmins"]
        assert messages["globalTitle"]
        assert messages["syncExisting"]


@pytest.mark.asyncio
async def test_privileged_intent_failure_records_actionable_recovery(monkeypatch):
    manager = DiscordBotManager()
    failed = asyncio.get_running_loop().create_future()
    failed.set_exception(discord.PrivilegedIntentsRequired(None))
    manager._runtimes[1] = SimpleNamespace(
        client_task=failed,
        renew_task=Mock(),
        lease_token="lease",
    )
    status = AsyncMock()
    monkeypatch.setattr(manager, "_update_bot_status", status)
    monkeypatch.setattr(
        "services.discord_bot_manager.redis_manager.release_lock", AsyncMock(return_value=True)
    )

    await manager._client_stopped(1, failed)

    error = status.await_args.args[2]
    assert status.await_args.args[1] == "error"
    assert "Message Content Intent" in error
    assert "mention-only" in error
