"""Discord Gateway Bot and server-level AI authorization regressions."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api.routes.discord_bot import (
    _bound_menu_push_channels,
    get_discord_menu_push_options,
    push_discord_menu,
)
from modules.models import (
    AuthType,
    DiscordOperationRun,
    Server,
    ServerDiscordBinding,
    User,
)
from modules.schemas.discord import (
    DiscordMenuPushRequest,
)
from modules.utils import get_current_time
from services.discord_bot_manager import (
    DiscordBotManager,
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
from services.discord_operation_service import (
    DiscordOperationDenied,
    canonical_payload,
    confirm_operation,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from tests.discord_bot_agent_policy_fakes import (
    _auth_fixtures as _auth_fixtures,
)
from tests.discord_bot_agent_policy_fakes import (
    _discord_http_error as _discord_http_error,
)
from tests.discord_bot_agent_policy_fakes import (
    _global_bot as _global_bot,
)


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
async def test_bound_menu_push_channels_include_enabled_global_binding():
    db = AsyncMock()
    result = Mock()
    result.all.return_value = []
    db.execute.return_value = result
    db.get = AsyncMock(return_value=_global_bot())

    assert await _bound_menu_push_channels(db, 1) == {"100": {"200"}}


@pytest.mark.asyncio
async def test_menu_options_list_all_guilds_and_filter_channels(monkeypatch):
    monkeypatch.setattr(
        "api.routes.discord_bot._stored_token",
        AsyncMock(return_value=(SimpleNamespace(), "secret-token")),
    )
    monkeypatch.setattr(
        "api.routes.discord_bot._bound_menu_push_channels",
        AsyncMock(return_value={"100": {"200"}}),
    )
    monkeypatch.setattr(
        "api.routes.discord_bot._load_discord_options",
        AsyncMock(
            return_value=(
                [{"id": "100", "name": "Bound"}, {"id": "101", "name": "Other"}],
                [
                    {"id": "200", "type": 0, "name": "ops"},
                    {"id": "201", "type": 0, "name": "chat"},
                ],
                [],
            )
        ),
    )

    listed = await get_discord_menu_push_options(AsyncMock(), SimpleNamespace(id=1), None)
    assert [item.id for item in listed.guilds] == ["100", "101"]

    unbound = await get_discord_menu_push_options(AsyncMock(), SimpleNamespace(id=1), "101")
    assert unbound.channels == []

    bound = await get_discord_menu_push_options(AsyncMock(), SimpleNamespace(id=1), "100")
    assert [item.id for item in bound.channels] == ["200"]


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
