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
    _bound_menu_push_channels,
    push_discord_menu,
    update_discord_global_binding,
)
from modules.models import (
    AuthType,
    DiscordOperationRun,
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
    DiscordGlobalBindingResponse,
    DiscordGlobalBindingUpdate,
    DiscordMenuPushRequest,
)
from modules.utils import get_current_time
from services.agent_policy_service import get_effective_agent_policy
from services.ai_security import redact_sensitive_text
from services.ai_tools import TOOLS_BY_NAME, GameConsoleCommandInput, tool_definitions
from services.discord_authorization_service import authorized_bindings
from services.discord_binding_template_service import (
    binding_matches_template,
    inherit_global_discord_binding,
    sync_global_discord_binding,
)
from services.discord_bot_manager import (
    DiscordBotManager,
    ManagedDiscordClient,
    _is_channel_manager,
    discord_bot_manager,
    status_card_fields,
)
from services.discord_bot_service import (
    DISCORD_COMMAND_CHANNEL_TYPES,
    DISCORD_COMPONENTS_V2_FLAG,
    DISCORD_MENU_PUSH_CHANNEL_TYPES,
    DISCORD_SUPPRESS_NOTIFICATIONS_FLAG,
    MINIMUM_BOT_PERMISSIONS,
    build_invite_url,
    get_guild_options,
    send_menu_launcher,
)
from services.discord_menu_ui import (
    control_view,
    is_exact_wake_word,
    launcher_is_expired,
    launcher_view,
    menu_is_expired,
    normalize_message_trigger,
    server_picker_view,
)
from services.discord_operation_service import (
    DiscordOperationDenied,
    canonical_payload,
    confirm_operation,
    create_operation,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
    assert managers_only.role_ids == []
    assert managers_only.user_ids == []
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
    game_console = TOOLS_BY_NAME["send_game_console_command"]
    assert game_console.required_capabilities({"command": "status"}) == frozenset(
        {AgentCapability.SEND_GAME_CONSOLE_COMMANDS}
    )
    assert game_console.is_exposed(readonly) is False


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


def _auth_fixtures(*, allow_channel_managers: bool = False):
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
        role_ids=[],
        user_ids=[],
        allow_channel_managers=allow_channel_managers,
        capabilities=["status"],
    )
    db = AsyncMock()
    db.get.side_effect = [bot, owner]
    result = AsyncMock()
    result.all = lambda: [(binding, server)]
    db.execute.return_value = result
    return db, binding, server


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


def test_status_card_includes_version_and_does_not_invent_missing_values():
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
    online = dict(
        status_card_fields(
            server,
            a2s_ok=True,
            info={
                "server_name": "CS2-ZE",
                "version": "1.41.1.1",
                "map_name": "ze_demon_slayer",
                "player_count": 1,
                "max_players": 64,
                "bot_count": 0,
                "game": "Counter-Strike 2",
                "platform": "l",
                "vac_enabled": True,
                "password_protected": False,
                "ping": 0.012,
            },
            locale="zh-CN",
        )
    )
    assert online["CS2 版本"] == "1.41.1.1"
    assert online["已记录版本"] == "1.40.9.4"
    assert online["地图"] == "ze_demon_slayer"
    assert online["玩家"] == "1/64 · Bot 0"
    assert online["查询地址"] == "fulldown.upkk.com:27015"
    assert "1.41.1.1" in online.values()

    offline = dict(status_card_fields(server, a2s_ok=False, info=None, locale="en-US"))
    assert offline["CS2 version"] == "1.40.9.4"
    assert offline["Configured map"] == "de_dust2"
    missing_version = server.model_copy(update={"current_game_version": None})
    unknown = dict(status_card_fields(missing_version, a2s_ok=False, info=None, locale="en-US"))
    assert unknown["CS2 version"] == "unknown"


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


def test_profile_guide_exposes_trigger_mode_and_bilingual_intent_warning():
    profile = (PROJECT_ROOT / "templates/profile.html").read_text(encoding="utf-8")
    assert 'id="discord-bot-trigger-mode"' in profile
    assert "message_trigger_mode" in profile
    assert "discord-bot-message-content-warning" in profile
    assert 'id="discord-menu-push-guild"' in profile
    assert 'id="discord-menu-push-channel"' in profile
    assert 'id="discord-global-binding-panel"' in profile
    assert 'id="discord-global-sync"' in profile
    assert 'id="discord-global-channel-managers"' in profile
    assert "allow_channel_managers" in profile
    assert "function discordBotText(" in profile
    assert "discordGlobalSelectedCapabilities()" in profile
    assert "refreshDiscordBotLocalizedText" in profile
    assert "/api/auth/discord-bot/menu-options" in profile
    assert "/api/auth/discord-bot/menu" in profile
    assert "/api/auth/discord-bot/global-settings" in profile
    assert "/api/auth/discord-bot/global-options" in profile
    for locale in ("en-US", "zh-CN"):
        messages = json.loads(
            (PROJECT_ROOT / f"static/locales/{locale}.json").read_text(encoding="utf-8")
        )
        assert messages["discordBot"]["triggerMentionOnly"]
        assert "Message Content Intent" in messages["discordBot"]["messageContentWarning"]
        assert messages["discordBot"]["pushMenuTitle"]
        assert messages["discordBot"]["globalSyncWarning"]
        assert messages["discordBot"]["allowChannelManagers"]
        assert messages["discordBot"]["capStatus"]
        assert messages["discordBot"]["whitelistRule"]


def test_friendly_menu_wake_words_are_exact_normalized_and_mention_safe():
    assert normalize_message_trigger("  <@!123> 你好！ ", 123) == "你好"
    assert is_exact_wake_word("你好。", 123) is True
    assert is_exact_wake_word(" ＨＥＬＬＯ! ", 123) is True
    assert is_exact_wake_word("你好大家", 123) is False
    assert is_exact_wake_word("please open menu", 123) is False
    assert menu_is_expired(100, now=999) is False
    assert menu_is_expired(100, now=1001) is True
    assert launcher_is_expired(100, now=400) is False
    assert launcher_is_expired(100, now=401) is True
    launcher = launcher_view("zh-CN", issued_at=123).to_components()
    assert launcher[0]["components"][3]["components"][0]["custom_id"] == "cs2:menu:open:123"


def test_components_v2_menu_filters_actions_and_paginates_servers():
    control = control_view(
        "zh-CN",
        server_id=10,
        server_name="测试服",
        capabilities=["status", "restart", "agent_ask"],
        issued_at=123,
    ).to_components()
    action_select = control[0]["components"][3]["components"][0]
    assert action_select["custom_id"] == "cs2:menu:action:123:10"
    assert {item["value"] for item in action_select["options"]} == {
        "status",
        "restart",
        "agent_ask",
        "agent_reset",
    }
    assert "stop" not in {item["value"] for item in action_select["options"]}

    servers = [
        {"id": index, "name": f"Server {index}", "capability_count": 2} for index in range(1, 46)
    ]
    picker = server_picker_view("en-US", servers, issued_at=123, page=1).to_components()
    server_select = picker[0]["components"][3]["components"][0]
    assert len(server_select["options"]) == 20
    assert server_select["options"][0]["value"] == "21"
    assert server_select["options"][-1]["value"] == "40"


@pytest.mark.asyncio
async def test_message_launcher_requires_trigger_authorization_and_rate_limit(monkeypatch):
    manager = DiscordBotManager()
    client = SimpleNamespace(
        owner_user_id=1,
        message_trigger_mode="mention_and_greetings",
        user=SimpleNamespace(id=999),
    )
    binding = ServerDiscordBinding(
        server_id=10,
        user_id=1,
        enabled=True,
        capabilities=["status"],
    )
    server = SimpleNamespace(id=10, name="Server")
    monkeypatch.setattr(
        manager, "_authorized_menu_pairs", AsyncMock(return_value=[(binding, server)])
    )
    monkeypatch.setattr(
        "services.discord_bot_manager.redis_manager.hit_rate_limit",
        AsyncMock(return_value=(True, 0)),
    )
    message = SimpleNamespace(
        guild=SimpleNamespace(id=100, preferred_locale="zh-CN"),
        channel=SimpleNamespace(id=200),
        author=SimpleNamespace(id=400, bot=False, roles=[]),
        webhook_id=None,
        raw_mentions=[],
        content="你好！",
        reply=AsyncMock(),
    )

    await manager.handle_message(client, message)

    message.reply.assert_awaited_once()
    assert message.reply.await_args.kwargs["delete_after"] == 300
    assert message.reply.await_args.kwargs["silent"] is True

    message.reply.reset_mock()
    client.message_trigger_mode = "mention_only"
    message.raw_mentions = []
    await manager.handle_message(client, message)
    message.reply.assert_not_awaited()

    message.content = "<@999> run an arbitrary action"
    message.raw_mentions = [999]
    await manager.handle_message(client, message)
    message.reply.assert_not_awaited()

    message.content = "<@!999> menu!"
    await manager.handle_message(client, message)
    message.reply.assert_awaited_once()

    manager._authorized_menu_pairs = AsyncMock(return_value=[])
    message.reply.reset_mock()
    await manager.handle_message(client, message)
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_operation_creation_rechecks_current_binding_and_capability(monkeypatch):
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
    db = AsyncMock()
    db.add = Mock()
    db.get.side_effect = [server, owner]
    monkeypatch.setattr(
        "services.discord_operation_service.authorized_bindings",
        AsyncMock(return_value=[]),
    )

    with pytest.raises(DiscordOperationDenied, match="authorization was revoked"):
        await create_operation(
            db,
            server=server,
            actor_user_id="400",
            actor_role_ids={"300"},
            guild_id="100",
            channel_id="200",
            action="restart",
            required_capabilities=[DiscordCapability.RESTART],
            arguments={"action": "restart"},
            plan={"server_id": 10, "action": "restart"},
        )
    db.commit.assert_not_awaited()


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


def _global_bot() -> UserDiscordBot:
    return UserDiscordBot(
        user_id=1,
        global_binding_configured=True,
        global_binding_enabled=True,
        global_guild_id="100",
        global_channel_ids=["200"],
        global_role_ids=["300"],
        global_user_ids=["400"],
        global_capabilities=["status", "restart"],
    )


@pytest.mark.asyncio
async def test_new_server_inherits_global_discord_template_without_committing():
    bot = _global_bot()
    server = Server(
        id=10,
        user_id=1,
        name="Server",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
    )
    db = AsyncMock()
    db.add = Mock()
    db.get.side_effect = [bot, None]

    binding = await inherit_global_discord_binding(db, server)

    assert binding is not None
    assert binding.enabled is True
    assert binding.guild_id == "100"
    assert binding.channel_ids == ["200"]
    assert binding.capabilities == ["status", "restart"]
    assert binding.allow_channel_managers is False
    assert binding_matches_template(binding, bot) is True
    db.add.assert_called_once_with(binding)
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_server_inherits_explicit_channel_manager_switch():
    bot = _global_bot()
    bot.global_allow_channel_managers = True
    server = Server(
        id=12,
        user_id=1,
        name="Server",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
    )
    db = AsyncMock()
    db.add = Mock()
    db.get.side_effect = [bot, None]

    binding = await inherit_global_discord_binding(db, server)

    assert binding is not None
    assert binding.allow_channel_managers is True
    assert binding_matches_template(binding, bot) is True


@pytest.mark.asyncio
async def test_explicit_global_sync_overwrites_every_owned_server_binding():
    bot = _global_bot()
    servers = [SimpleNamespace(id=10), SimpleNamespace(id=11)]
    customized = ServerDiscordBinding(
        server_id=10,
        user_id=1,
        enabled=False,
        guild_id="999",
        channel_ids=["998"],
        role_ids=["997"],
        capabilities=["stop"],
    )
    server_result = Mock()
    server_result.scalars.return_value.all.return_value = servers
    binding_result = Mock()
    binding_result.scalars.return_value.all.return_value = [customized]
    db = AsyncMock()
    db.add = Mock()
    db.execute.side_effect = [server_result, binding_result]

    count = await sync_global_discord_binding(db, bot)

    assert count == 2
    assert binding_matches_template(customized, bot) is True
    created = next(call.args[0] for call in db.add.call_args_list if call.args[0].server_id == 11)
    assert binding_matches_template(created, bot) is True
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_api_syncs_only_when_explicitly_requested(monkeypatch):
    bot = _global_bot()
    bot.global_binding_configured = False
    monkeypatch.setattr(
        "api.routes.discord_bot._stored_token", AsyncMock(return_value=(bot, "secret-token"))
    )
    monkeypatch.setattr(
        "api.routes.discord_bot._validate_binding_selection", AsyncMock(return_value=None)
    )
    sync = AsyncMock(return_value=3)
    monkeypatch.setattr("api.routes.discord_bot.sync_global_discord_binding", sync)
    response = DiscordGlobalBindingResponse(
        configured=True,
        enabled=True,
        guild_id="100",
        channel_ids=["200"],
        role_ids=["300"],
        capabilities=[DiscordCapability.STATUS],
        server_count=3,
        matching_server_count=3,
        synced_server_count=3,
    )
    monkeypatch.setattr(
        "api.routes.discord_bot._global_binding_response", AsyncMock(return_value=response)
    )
    notify = AsyncMock()
    monkeypatch.setattr("api.routes.discord_bot._notify_manager", notify)
    db = AsyncMock()
    db.add = Mock()

    result = await update_discord_global_binding(
        DiscordGlobalBindingUpdate(
            enabled=True,
            guild_id="100",
            channel_ids=["200"],
            role_ids=["300"],
            capabilities=[DiscordCapability.STATUS],
            sync_existing_servers=True,
        ),
        db,
        SimpleNamespace(id=1),
    )

    assert result.synced_server_count == 3
    assert bot.global_binding_configured is True
    assert bot.global_channel_ids == ["200"]
    sync.assert_awaited_once_with(db, bot)
    db.commit.assert_awaited_once()
    notify.assert_awaited_once_with(1)

    sync.reset_mock()
    notify.reset_mock()
    await update_discord_global_binding(
        DiscordGlobalBindingUpdate(
            enabled=True,
            guild_id="100",
            channel_ids=["200"],
            role_ids=["300"],
            capabilities=[DiscordCapability.STATUS],
            sync_existing_servers=False,
        ),
        db,
        SimpleNamespace(id=1),
    )
    sync.assert_not_awaited()
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_bound_menu_push_channels_fail_closed():
    good = ServerDiscordBinding(
        server_id=10,
        user_id=1,
        enabled=True,
        guild_id="100",
        channel_ids=["200", "201"],
        capabilities=["status"],
    )
    invalid = good.model_copy(
        update={
            "server_id": 11,
            "channel_ids": ["202"],
            "invalid_reason": "command_sync_failed",
        }
    )
    empty = good.model_copy(update={"server_id": 12, "channel_ids": ["203"], "capabilities": []})
    cross_owner = good.model_copy(update={"server_id": 13, "channel_ids": ["204"]})
    db = AsyncMock()
    result = Mock()
    result.all.return_value = [
        (good, SimpleNamespace(user_id=1)),
        (invalid, SimpleNamespace(user_id=1)),
        (empty, SimpleNamespace(user_id=1)),
        (cross_owner, SimpleNamespace(user_id=2)),
    ]
    db.execute.return_value = result

    assert await _bound_menu_push_channels(db, 1) == {"100": {"200", "201"}}


@pytest.mark.asyncio
async def test_rest_menu_push_uses_components_v2_silent_nonce(monkeypatch):
    request = AsyncMock(return_value=(True, {"id": "987654321098765432"}, None))
    monkeypatch.setattr("services.discord_bot_service.http_helper.post", request)

    message_id, issued_at = await send_menu_launcher("secret-token", "223", "zh-CN")

    assert message_id == "987654321098765432"
    payload = request.await_args.kwargs["json"]
    assert payload["flags"] == DISCORD_COMPONENTS_V2_FLAG | DISCORD_SUPPRESS_NOTIFICATIONS_FLAG
    assert payload["allowed_mentions"] == {"parse": []}
    assert payload["enforce_nonce"] is True
    assert len(payload["nonce"]) == 25
    button = payload["components"][0]["components"][3]["components"][0]
    assert button["custom_id"] == f"cs2:menu:open:{issued_at}"
    assert "secret-token" not in str(payload)


@pytest.mark.asyncio
async def test_rest_channel_options_include_active_threads(monkeypatch):
    fetch = AsyncMock(
        side_effect=[
            [{"id": "100", "name": "Guild"}],
            [{"id": "200", "name": "general", "type": 0}],
            {"threads": [{"id": "201", "name": "forum-post", "type": 11}]},
            [{"id": "300", "name": "Operator"}],
        ]
    )
    monkeypatch.setattr("services.discord_bot_service._get", fetch)

    channels, roles = await get_guild_options("secret-token", "100")

    assert [item["id"] for item in channels] == ["200", "201"]
    assert [item["id"] for item in roles] == ["300"]


@pytest.mark.asyncio
async def test_profile_can_push_only_to_a_bound_message_channel(monkeypatch):
    monkeypatch.setattr(
        "api.routes.discord_bot._connected_bot_token",
        AsyncMock(return_value=(SimpleNamespace(), "secret-token")),
    )
    monkeypatch.setattr(
        "api.routes.discord_bot._bound_menu_push_channels",
        AsyncMock(return_value={"100": {"200"}}),
    )
    monkeypatch.setattr(
        "api.routes.discord_bot._load_discord_options",
        AsyncMock(return_value=([{"id": "100"}], [{"id": "200", "type": 0}], [])),
    )
    monkeypatch.setattr(
        "api.routes.discord_bot.redis_manager.hit_rate_limit",
        AsyncMock(return_value=(True, 0)),
    )
    monkeypatch.setattr("api.routes.discord_bot.get_guild_locale", AsyncMock(return_value="zh-CN"))
    sender = AsyncMock(return_value=("300", 123))
    monkeypatch.setattr("api.routes.discord_bot.send_menu_launcher", sender)

    def close_cleanup(coroutine):
        coroutine.close()

    monkeypatch.setattr("api.routes.discord_bot.discord_menu_task_registry.create", close_cleanup)
    response = await push_discord_menu(
        DiscordMenuPushRequest(guild_id="100", channel_id="200"),
        AsyncMock(),
        SimpleNamespace(id=1),
    )

    assert response.message_id == "300"
    assert response.expires_in_seconds == 300
    sender.assert_awaited_once_with("secret-token", "200", "zh-CN")
    assert {0, 2, 5, 10, 11, 12, 13} == DISCORD_MENU_PUSH_CHANNEL_TYPES


def test_gateway_configuration_options_include_usable_channel_types_only():
    manager = DiscordBotManager()
    allowed_permissions = SimpleNamespace(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        read_message_history=True,
    )
    denied_permissions = SimpleNamespace(
        view_channel=True,
        send_messages=False,
        embed_links=True,
        read_message_history=True,
    )

    def channel(channel_id: int, channel_type: int, permissions):
        return SimpleNamespace(
            id=channel_id,
            name=f"channel-{channel_id}",
            type=SimpleNamespace(value=channel_type),
            permissions_for=lambda _member: permissions,
        )

    guild = SimpleNamespace(
        id=100,
        name="Guild",
        icon=None,
        me=object(),
        channels=[
            channel(200, 0, allowed_permissions),
            channel(201, 15, allowed_permissions),
            channel(202, 0, denied_permissions),
            channel(203, 4, allowed_permissions),
        ],
        threads=[],
        roles=[SimpleNamespace(id=300, name="Operator", position=1)],
    )
    client = Mock()
    client.is_ready.return_value = True
    client.guilds = [guild]
    client.get_guild.return_value = guild
    manager._runtimes[1] = SimpleNamespace(client=client)

    snapshot = manager.configuration_options(1, "100")

    assert snapshot is not None
    assert [item["id"] for item in snapshot["channels"]] == ["200", "201"]
    assert snapshot["roles"] == [{"id": "300", "name": "Operator", "position": 1}]
    assert {2, 13, 15, 16}.issubset(DISCORD_COMMAND_CHANNEL_TYPES)


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
