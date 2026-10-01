"""Discord Gateway Bot and server-level AI authorization regressions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from modules.models import (
    AuthType,
    Server,
    ServerDiscordBinding,
    User,
)
from modules.schemas.discord import (
    DiscordCapability,
)
from services.discord_ai_service import (
    available_discord_agent_server_ids,
    resolve_discord_agent_server,
)
from services.discord_bot_manager import (
    DiscordBotManager,
)
from services.discord_menu_ui import (
    control_view,
    is_exact_wake_word,
    is_leading_bot_mention,
    launcher_is_expired,
    launcher_view,
    leading_bot_mention_content,
    mention_trigger_content,
    menu_is_expired,
    menu_issued_at,
    normalize_message_trigger,
    server_picker_view,
)
from services.discord_operation_service import (
    DiscordOperationDenied,
    create_operation,
)

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


def test_friendly_menu_wake_words_are_exact_normalized_and_mention_safe():
    assert normalize_message_trigger("  <@!123> 你好！ ", 123) == "你好"
    assert is_exact_wake_word("你好。", 123) is True
    assert is_exact_wake_word(" ＨＥＬＬＯ! ", 123) is True
    assert is_exact_wake_word("你好大家", 123) is False
    assert is_exact_wake_word("please open menu", 123) is False
    assert is_leading_bot_mention("  <@123>", 123) is True
    assert is_leading_bot_mention("\n<@!123> run an arbitrary action", 123) is True
    assert is_leading_bot_mention("please ask <@123>", 123) is False
    assert is_leading_bot_mention("<@456> <@123>", 123) is False
    assert leading_bot_mention_content(" <@!123>  ze1 执行 meta list ", 123) == (
        "ze1 执行 meta list"
    )
    assert leading_bot_mention_content("please ask <@123>", 123) is None
    assert mention_trigger_content("<@123>", 123, mentioned=True) == ""
    assert mention_trigger_content("", 123, mentioned=True) == ""
    assert mention_trigger_content("", 123, mentioned=False) is None
    assert mention_trigger_content("please ask <@123>", 123, mentioned=True) is None
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
        requester_user_id=400,
    ).to_components()
    action_select = control[0]["components"][3]["components"][0]
    assert action_select["custom_id"] == "cs2:menu:action:123:400:10"
    assert {item["value"] for item in action_select["options"]} == {
        "status",
        "restart",
        "agent_ask",
        "agent_reset",
    }
    assert "stop" not in {item["value"] for item in action_select["options"]}
    assert "change_map" not in {item["value"] for item in action_select["options"]}

    map_control = control_view(
        "zh-CN",
        server_id=10,
        server_name="测试服",
        capabilities=["change_map"],
        issued_at=123,
        requester_user_id=400,
    ).to_components()
    map_options = {
        item["value"] for item in map_control[0]["components"][3]["components"][0]["options"]
    }
    assert map_options == {"change_map"}

    servers = [
        {"id": index, "name": f"Server {index}", "capability_count": 2} for index in range(1, 46)
    ]
    picker = server_picker_view(
        "en-US", servers, issued_at=123, requester_user_id=400, page=1
    ).to_components()
    server_select = picker[0]["components"][3]["components"][0]
    assert server_select["custom_id"] == "cs2:menu:server:123:400:1"
    assert len(server_select["options"]) == 20
    assert server_select["options"][0]["value"] == "21"
    assert server_select["options"][-1]["value"] == "40"


def test_discord_agent_server_resolution_uses_unique_authorized_name_aliases():
    ze1 = SimpleNamespace(id=10, name="[UPKK] CS2 ZE #1")
    ze2 = SimpleNamespace(id=11, name="[UPKK] CS2 ZE #2")
    kz1 = SimpleNamespace(id=12, name="[UPKK] CS2 KZ #1")
    plain_ze = SimpleNamespace(id=13, name="CS2-ZE")

    assert resolve_discord_agent_server("发送 meta list 到 ze1", [ze1, ze2, kz1]) is ze1
    assert resolve_discord_agent_server("查询 KZ 1 当前状态", [ze1, ze2, kz1]) is kz1
    assert resolve_discord_agent_server("查询 ze 服务器", [ze1, ze2, kz1]) is None
    assert resolve_discord_agent_server("ze1 执行 meta list", [plain_ze, kz1]) is plain_ze
    assert resolve_discord_agent_server("回报当前状态", [ze1]) is ze1
    assert resolve_discord_agent_server("回报当前状态", [ze1, kz1]) is None


@pytest.mark.asyncio
async def test_available_discord_agent_servers_require_owner_provider_and_enabled_policy(
    monkeypatch,
):
    owner = SimpleNamespace(id=1, is_active=True)
    db = AsyncMock()
    db.get.return_value = owner
    result = Mock()
    result.scalars.return_value.all.return_value = [10, 11]
    db.execute.return_value = result

    class Session:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr("services.discord_ai_service.async_session_maker", Session)
    provider = AsyncMock(return_value=SimpleNamespace(model="test"))
    policy = AsyncMock(side_effect=[SimpleNamespace(enabled=True), SimpleNamespace(enabled=False)])
    monkeypatch.setattr("services.discord_ai_service.get_effective_provider", provider)
    monkeypatch.setattr("services.discord_ai_service.get_effective_agent_policy", policy)

    available = await available_discord_agent_server_ids(owner_user_id=1, server_ids=[10, 11, 999])

    assert available == frozenset({10})
    provider.assert_awaited_once_with(db, owner)
    assert policy.await_count == 2


@pytest.mark.asyncio
async def test_message_menu_requires_trigger_authorization_and_rate_limit(monkeypatch):
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
    rate_limit = AsyncMock(return_value=(True, 0))
    monkeypatch.setattr("services.discord_bot_manager.redis_manager.hit_rate_limit", rate_limit)
    monkeypatch.setattr(manager, "_message_agent_server", AsyncMock(return_value=None))
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
    assert message.reply.await_args.kwargs["delete_after"] == 900
    assert message.reply.await_args.kwargs["silent"] is True
    assert message.reply.await_args.kwargs["mention_author"] is False
    assert message.reply.await_args.kwargs["allowed_mentions"].to_dict() == {"parse": []}
    action_id = message.reply.await_args.kwargs["view"].to_components()[0]["components"][3][
        "components"
    ][0]["custom_id"]
    action_parts = action_id.split(":")
    assert action_parts[:3] == ["cs2", "menu", "action"]
    assert action_parts[4:] == ["400", "10"]
    assert int(action_parts[3]) > 0

    message.reply.reset_mock()
    client.message_trigger_mode = "mention_only"
    message.raw_mentions = []
    await manager.handle_message(client, message)
    message.reply.assert_not_awaited()

    message.content = "<@999> run an arbitrary action"
    message.raw_mentions = [999]
    await manager.handle_message(client, message)
    message.reply.assert_awaited_once()

    message.reply.reset_mock()
    message.content = "please ask <@999>"
    await manager.handle_message(client, message)
    message.reply.assert_not_awaited()

    message.reply.reset_mock()
    message.content = ""
    message.raw_mentions = []
    message.mentions = [SimpleNamespace(id=999)]
    await manager.handle_message(client, message)
    message.reply.assert_awaited_once()

    message.reply.reset_mock()
    message.mentions = []
    message.content = "<@!999> menu!"
    message.raw_mentions = [999]
    await manager.handle_message(client, message)
    message.reply.assert_awaited_once()

    message.reply.reset_mock()
    rate_limit.return_value = (False, 5)
    await manager.handle_message(client, message)
    message.reply.assert_not_awaited()

    rate_limit.return_value = (True, 0)
    manager._authorized_menu_pairs = AsyncMock(return_value=[])
    message.reply.reset_mock()
    await manager.handle_message(client, message)
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_menu_ignores_untrusted_message_sources(monkeypatch):
    manager = DiscordBotManager()
    client = SimpleNamespace(
        owner_user_id=1,
        message_trigger_mode="mention_only",
        user=SimpleNamespace(id=999),
    )
    authorization = AsyncMock(side_effect=AssertionError("authorization must not run"))
    monkeypatch.setattr(manager, "_authorized_menu_pairs", authorization)
    reply = AsyncMock()

    messages = [
        SimpleNamespace(
            guild=None,
            webhook_id=None,
            author=SimpleNamespace(id=400, bot=False, roles=[]),
        ),
        SimpleNamespace(
            guild=SimpleNamespace(id=100),
            webhook_id=123,
            author=SimpleNamespace(id=400, bot=False, roles=[]),
        ),
        SimpleNamespace(
            guild=SimpleNamespace(id=100),
            webhook_id=None,
            author=SimpleNamespace(id=400, bot=True, roles=[]),
        ),
    ]
    for message in messages:
        message.channel = SimpleNamespace(id=200)
        message.raw_mentions = [999]
        message.content = "<@999>"
        message.reply = reply
        await manager.handle_message(client, message)

    authorization.assert_not_awaited()
    reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_menu_components_are_bound_to_the_requester(monkeypatch):
    manager = DiscordBotManager()
    client = SimpleNamespace(owner_user_id=1)
    issued_at = menu_issued_at()
    view = Mock()
    private_menu = AsyncMock(return_value=view)
    monkeypatch.setattr(manager, "_private_menu_view", private_menu)

    owner_interaction = SimpleNamespace(
        user=SimpleNamespace(id=400),
        guild=SimpleNamespace(preferred_locale="zh-CN"),
        locale="zh-CN",
        response=SimpleNamespace(edit_message=AsyncMock()),
    )
    await manager._handle_menu_component(
        client,
        owner_interaction,
        ["cs2", "menu", "page", str(issued_at), "400", "1"],
    )

    private_menu.assert_awaited_once_with(client, owner_interaction, issued_at=issued_at, page=1)
    owner_interaction.response.edit_message.assert_awaited_once_with(view=view)

    other_interaction = SimpleNamespace(
        user=SimpleNamespace(id=401),
        guild=SimpleNamespace(preferred_locale="zh-CN"),
        locale="zh-CN",
        response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    await manager.handle_component(
        client,
        other_interaction,
        f"cs2:menu:page:{issued_at}:400:1",
    )

    sent = other_interaction.response.send_message.await_args
    assert "仅限" in sent.args[0]
    assert sent.kwargs["ephemeral"] is True
    assert private_menu.await_count == 1


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
