"""Cover Discord settings routes with deterministic database and REST doubles."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes import discord_bot as routes
from modules import (
    AgentCapability,
    AgentPolicyUpdate,
    DiscordBindingUpdate,
    DiscordBotSettingsUpdate,
    DiscordBotTestRequest,
    DiscordCapability,
    DiscordGlobalBindingUpdate,
    DiscordMenuPushRequest,
    Server,
    ServerAgentPolicy,
    ServerDiscordBinding,
    UserDiscordBot,
)
from services.discord_bot_service import DiscordBotAPIError


class _Result:
    def __init__(self, value=None, rows=()):
        self.value = value
        self.rows = list(rows)

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _DB:
    def __init__(self, *, gets=None, results=()):
        self.gets = dict(gets or {})
        self.results = list(results)
        self.added = []
        self.commits = 0

    async def get(self, model, key):
        return self.gets.get((model, key), self.gets.get(model))

    async def execute(self, _statement):
        return self.results.pop(0) if self.results else _Result()

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value):
        return None

    async def scalar(self, _statement):
        return 2


def _user(**overrides):
    values = {"id": 9, "is_admin": False}
    values.update(overrides)
    return SimpleNamespace(**values)


def _server(**overrides):
    values = {"id": 11, "user_id": 9, "name": "srv"}
    values.update(overrides)
    return SimpleNamespace(**values)


def _bot(**overrides) -> UserDiscordBot:
    values = dict(
        user_id=9,
        token_encrypted="encrypted",
        enabled=True,
        connection_status="connected",
        application_id="100",
        bot_user_id="101",
        username="bot",
        global_binding_configured=False,
        global_binding_enabled=False,
    )
    values.update(overrides)
    return UserDiscordBot(**values)


def test_discord_route_helpers_cover_normalization_and_binding_states():
    assert routes._discord_capability("status") == DiscordCapability.STATUS
    assert routes._discord_capability("unknown") is None
    assert routes._trigger_mode("mention_and_greetings") == "mention_and_greetings"
    assert routes._trigger_mode("bad") == "mention_only"
    assert routes._bot_response(None).connection_status == "not_configured"
    assert routes._bot_response(_bot()).token_configured is True

    db = _DB(gets={UserDiscordBot: _bot()})
    binding = ServerDiscordBinding(
        server_id=11,
        user_id=9,
        enabled=True,
        guild_id="20",
        channel_ids=["30"],
        capabilities=["status"],
    )
    server = _server()
    db.results = [_Result(rows=[(binding, server)])]
    db.gets[UserDiscordBot] = _bot(
        global_binding_configured=True,
        global_binding_enabled=True,
        global_guild_id="21",
        global_channel_ids=["31", None],
        global_capabilities=["status", "bad"],
    )
    channels = routes._bound_menu_push_channels
    import asyncio

    payload = asyncio.run(channels(db, 9))
    assert payload == {"20": {"30"}, "21": {"31"}}


@pytest.mark.asyncio
async def test_discord_storage_and_option_fallback_branches(monkeypatch):
    user = _user()
    with pytest.raises(HTTPException) as missing:
        await routes._stored_token(_DB(gets={UserDiscordBot: None}), user.id)
    assert missing.value.status_code == 409
    monkeypatch.setattr(routes, "decrypt_credential", lambda _value: None)
    with pytest.raises(HTTPException) as unavailable:
        await routes._stored_token(_DB(gets={UserDiscordBot: _bot()}), user.id)
    assert unavailable.value.status_code == 409

    disabled = _bot(enabled=False)
    with pytest.raises(HTTPException) as not_connected:
        await routes._connected_bot_token(_DB(gets={UserDiscordBot: disabled}), user.id)
    assert not_connected.value.status_code == 409

    import services.discord_bot_manager as manager_module

    monkeypatch.setattr(
        manager_module,
        "discord_bot_manager",
        SimpleNamespace(configuration_options=lambda *_args: {"guild_missing": True}),
    )
    with pytest.raises(DiscordBotAPIError, match="not a member"):
        await routes._load_discord_options(user.id, "token", "20")
    monkeypatch.setattr(
        manager_module,
        "discord_bot_manager",
        SimpleNamespace(configuration_options=lambda *_args: None),
    )
    monkeypatch.setattr(routes, "list_guilds", AsyncMock(return_value=[{"id": "1"}]))
    assert await routes._load_discord_options(user.id, "token") == ([{"id": "1"}], [], [])
    monkeypatch.setattr(
        routes, "get_guild_options", AsyncMock(return_value=([{"id": "2"}], [{"id": "3"}]))
    )
    assert await routes._load_discord_options(user.id, "token", "1") == (
        [{"id": "1"}],
        [{"id": "2"}],
        [{"id": "3"}],
    )

    monkeypatch.setattr(
        routes, "_load_discord_options", AsyncMock(side_effect=DiscordBotAPIError("bad"))
    )
    with pytest.raises(HTTPException) as bad_role:
        await routes._validate_binding_selection(
            user.id, "token", guild_id="1", channel_ids=[], role_ids=[]
        )
    assert bad_role.value.status_code == 400
    monkeypatch.setattr(
        routes,
        "_load_discord_options",
        AsyncMock(return_value=([], [{"id": "2", "type": 0}], [{"id": "3"}])),
    )
    with pytest.raises(HTTPException) as role_error:
        await routes._validate_binding_selection(
            user.id, "token", guild_id="1", channel_ids=[], role_ids=["9"]
        )
    assert role_error.value.status_code == 422


@pytest.mark.asyncio
async def test_discord_option_helpers_cover_cache_rest_validation_and_errors(monkeypatch):
    user_id = 9
    token = "x"
    cached = {"guilds": [{"id": "1", "name": "Guild"}], "channels": [], "roles": []}
    monkeypatch.setattr(
        routes, "_load_discord_options", AsyncMock(return_value=(cached["guilds"], [], []))
    )
    result = await routes._discord_options_response(user_id, token, None)
    assert result.guilds[0].name == "Guild"
    monkeypatch.setattr(
        routes,
        "_load_discord_options",
        AsyncMock(
            return_value=(
                [{"id": "1"}],
                [{"id": "2", "name": "text", "type": 0}, {"id": "3", "type": 99}],
                [{"id": "4", "name": "role", "position": 2}, {"id": "1"}],
            )
        ),
    )
    result = await routes._discord_options_response(user_id, token, "1")
    assert [item.id for item in result.channels] == ["2"]
    assert [item.id for item in result.roles] == ["4"]
    monkeypatch.setattr(
        routes,
        "_load_discord_options",
        AsyncMock(side_effect=DiscordBotAPIError("REST unavailable")),
    )
    with pytest.raises(HTTPException) as exc:
        await routes._discord_options_response(user_id, token, "1")
    assert exc.value.status_code == 400

    monkeypatch.setattr(
        routes, "_load_discord_options", AsyncMock(return_value=([], [{"id": "2", "type": 0}], []))
    )
    await routes._validate_binding_selection(
        user_id, token, guild_id=None, channel_ids=[], role_ids=[]
    )
    await routes._validate_binding_selection(
        user_id, token, guild_id="1", channel_ids=["2"], role_ids=[]
    )
    with pytest.raises(HTTPException) as bad_channel:
        await routes._validate_binding_selection(
            user_id, token, guild_id="1", channel_ids=["9"], role_ids=[]
        )
    assert bad_channel.value.status_code == 422


@pytest.mark.asyncio
async def test_discord_bot_crud_test_and_options_routes(monkeypatch):
    user = _user()
    identity = SimpleNamespace(
        application_id="100", bot_user_id="101", username="bot", discriminator="0"
    )
    monkeypatch.setattr(routes, "test_bot_token", AsyncMock(return_value=identity))
    monkeypatch.setattr(routes, "encrypt_credential", lambda value: f"enc:{value}")
    monkeypatch.setattr(routes, "decrypt_credential", lambda _value: "token")
    monkeypatch.setattr(
        routes, "build_invite_url", lambda value: f"invite:{value}" if value else None
    )
    monkeypatch.setattr(routes, "_notify_manager", AsyncMock())
    monkeypatch.setattr(routes, "record_audit_event", AsyncMock())
    bot = _bot()
    db = _DB(gets={UserDiscordBot: None}, results=[_Result(value=None)])
    created = await routes.update_discord_bot(
        DiscordBotSettingsUpdate(
            token="t" * 20, enabled=True, message_trigger_mode="mention_and_greetings"
        ),
        db,
        user,
        SimpleNamespace(),
    )
    assert created.enabled and created.token_configured

    db = _DB(gets={UserDiscordBot: bot}, results=[_Result(rows=[])])
    deleted_binding = ServerDiscordBinding(server_id=11, user_id=9, enabled=True)
    db.results = [_Result(rows=[deleted_binding])]
    deleted = await routes.delete_discord_bot(db, user, SimpleNamespace())
    assert deleted.enabled is False and deleted_binding.invalid_reason == "bot_token_missing"

    success = await routes.test_discord_bot(DiscordBotTestRequest(token="t" * 20), db, user)
    assert success.success
    monkeypatch.setattr(
        routes, "test_bot_token", AsyncMock(side_effect=DiscordBotAPIError("invalid"))
    )
    failure = await routes.test_discord_bot(DiscordBotTestRequest(token="t" * 20), db, user)
    assert failure.success is False

    monkeypatch.setattr(routes, "_stored_token", AsyncMock(return_value=(bot, "token")))
    monkeypatch.setattr(
        routes, "_load_discord_options", AsyncMock(return_value=([{"id": "1"}], [], []))
    )
    guilds = await routes.get_discord_bot_guilds(db, user)
    assert guilds[0].id == "1"
    options = await routes.get_discord_global_binding_options(db, user, None)
    assert options.guilds[0].id == "1"

    monkeypatch.setattr(
        routes, "test_bot_token", AsyncMock(side_effect=DiscordBotAPIError("invalid"))
    )
    with pytest.raises(HTTPException) as update_error:
        await routes.update_discord_bot(
            DiscordBotSettingsUpdate(token="t" * 20),
            _DB(gets={UserDiscordBot: bot}, results=[_Result(value=None)]),
            user,
            SimpleNamespace(),
        )
    assert update_error.value.status_code == 400
    with pytest.raises(HTTPException) as enable_error:
        await routes.update_discord_bot(
            DiscordBotSettingsUpdate(enabled=True),
            _DB(gets={UserDiscordBot: UserDiscordBot(user_id=9)}),
            user,
            SimpleNamespace(),
        )
    assert enable_error.value.status_code == 409
    assert (
        await routes.delete_discord_bot(_DB(gets={UserDiscordBot: None}), user, SimpleNamespace())
    ).connection_status == "not_configured"
    monkeypatch.setattr(
        routes, "_load_discord_options", AsyncMock(side_effect=DiscordBotAPIError("gone"))
    )
    with pytest.raises(HTTPException) as guild_error:
        await routes.get_discord_bot_guilds(db, user)
    assert guild_error.value.status_code == 400


@pytest.mark.asyncio
async def test_global_binding_server_binding_and_agent_routes(monkeypatch):
    user = _user()
    bot = _bot()
    server = _server()
    monkeypatch.setattr(routes, "_stored_token", AsyncMock(return_value=(bot, "token")))
    monkeypatch.setattr(routes, "_validate_binding_selection", AsyncMock())
    monkeypatch.setattr(routes, "sync_global_discord_binding", AsyncMock(return_value=1))
    monkeypatch.setattr(routes, "global_binding_counts", AsyncMock(return_value=(2, 1)))
    monkeypatch.setattr(routes, "_notify_manager", AsyncMock())
    monkeypatch.setattr(routes, "record_audit_event", AsyncMock())
    binding = ServerDiscordBinding(server_id=11, user_id=9, enabled=False)
    db = _DB(gets={UserDiscordBot: bot, ServerDiscordBinding: binding, Server: server})
    request = DiscordGlobalBindingUpdate(
        enabled=True,
        guild_id="20",
        channel_ids=["30"],
        allow_server_administrators=True,
        capabilities=[DiscordCapability.STATUS],
        sync_existing_servers=True,
    )
    global_result = await routes.update_discord_global_binding(request, db, user, SimpleNamespace())
    assert global_result.synced_server_count == 1
    assert global_result.capabilities == [DiscordCapability.STATUS]

    monkeypatch.setattr(routes, "_owned_server", AsyncMock(return_value=server))
    monkeypatch.setattr(
        routes, "_binding_response", AsyncMock(return_value=SimpleNamespace(server_id=11))
    )
    server_result = await routes.update_server_discord_bot_settings(
        11, DiscordBindingUpdate(enabled=False), db, user, SimpleNamespace()
    )
    assert server_result.server_id == 11
    monkeypatch.setattr(routes, "_discord_options_response", AsyncMock(return_value="options"))
    assert await routes.get_server_discord_bot_options(11, db, user) == "options"

    policy = SimpleNamespace(enabled=True, capabilities={AgentCapability.INSPECT_STATUS})
    disabled_policy = SimpleNamespace(enabled=False, capabilities={AgentCapability.INSPECT_STATUS})
    monkeypatch.setattr(
        routes, "get_effective_agent_policy", AsyncMock(side_effect=[policy, disabled_policy])
    )
    monkeypatch.setattr(routes, "get_effective_provider", AsyncMock(return_value=object()))
    monkeypatch.setattr(routes, "require_server_access", AsyncMock(return_value=server))
    assert (await routes.get_server_agent_policy(11, db, user)).effective_enabled
    db.gets[ServerAgentPolicy] = ServerAgentPolicy(server_id=11, enabled=False)
    policy_update = await routes.update_server_agent_policy(
        11,
        AgentPolicyUpdate(enabled=False, capabilities=[AgentCapability.INSPECT_STATUS]),
        db,
        user,
        SimpleNamespace(),
    )
    assert policy_update.disabled_reason == "policy_disabled"


@pytest.mark.asyncio
async def test_menu_push_and_binding_response_failure_paths(monkeypatch):
    user = _user()
    bot = _bot()
    binding = ServerDiscordBinding(
        server_id=11,
        user_id=9,
        enabled=True,
        guild_id="20",
        channel_ids=["30"],
        capabilities=["status"],
    )
    db = _DB(gets={UserDiscordBot: bot}, results=[_Result(rows=[(binding, _server())])])
    monkeypatch.setattr(routes, "_stored_token", AsyncMock(return_value=(bot, "token")))
    monkeypatch.setattr(routes, "_bound_menu_push_channels", AsyncMock(return_value={"20": {"30"}}))
    monkeypatch.setattr(
        routes,
        "_load_discord_options",
        AsyncMock(return_value=([{"id": "20"}], [{"id": "30", "type": 0}], [])),
    )
    monkeypatch.setattr(routes.redis_manager, "hit_rate_limit", AsyncMock(return_value=(True, 0)))
    monkeypatch.setattr(routes, "get_guild_locale", AsyncMock(return_value="en-US"))
    monkeypatch.setattr(routes, "send_menu_launcher", AsyncMock(return_value=("55", 1)))
    monkeypatch.setattr(routes.discord_menu_task_registry, "create", lambda _task: None)
    monkeypatch.setattr(routes, "delete_menu_launcher_after", lambda *_args: None)
    pushed = await routes.push_discord_menu(
        DiscordMenuPushRequest(guild_id="20", channel_id="30"), db, user
    )
    assert pushed.message_id == "55"

    monkeypatch.setattr(
        routes, "_load_discord_options", AsyncMock(side_effect=DiscordBotAPIError("down"))
    )
    with pytest.raises(HTTPException) as exc:
        await routes.push_discord_menu(
            DiscordMenuPushRequest(guild_id="20", channel_id="30"), db, user
        )
    assert exc.value.status_code == 400

    for kwargs in (
        {"enabled": False},
        {"enabled": True, "token_encrypted": None},
        {"enabled": True, "token_encrypted": "x", "connection_status": "bad"},
    ):
        result = await routes._binding_response(
            _DB(gets={ServerDiscordBinding: binding, UserDiscordBot: _bot(**kwargs)}), 11, 9
        )
        assert result.disabled_reason is not None

    assert (
        await routes._binding_response(
            _DB(gets={ServerDiscordBinding: None, UserDiscordBot: bot}), 11, 9
        )
    ).disabled_reason == "binding_disabled"
    invalid_binding = ServerDiscordBinding(
        server_id=11, user_id=9, enabled=True, invalid_reason="sync_failed"
    )
    assert (
        await routes._binding_response(
            _DB(gets={ServerDiscordBinding: invalid_binding, UserDiscordBot: bot}), 11, 9
        )
    ).disabled_reason == "sync_failed"

    monkeypatch.setattr(routes, "_bound_menu_push_channels", AsyncMock(return_value={}))
    monkeypatch.setattr(
        routes, "_load_discord_options", AsyncMock(side_effect=DiscordBotAPIError("down"))
    )
    with pytest.raises(HTTPException) as no_fallback:
        await routes.get_discord_menu_push_options(db, user)
    assert no_fallback.value.status_code == 400
    monkeypatch.setattr(routes, "_bound_menu_push_channels", AsyncMock(return_value={"20": {"30"}}))
    fallback = await routes.get_discord_menu_push_options(db, user)
    assert fallback.guilds[0].id == "20"

    with pytest.raises(HTTPException) as channel_error:
        await routes.push_discord_menu(
            DiscordMenuPushRequest(guild_id="20", channel_id="99"), db, user
        )
    assert channel_error.value.status_code == 422
    monkeypatch.setattr(
        routes,
        "_load_discord_options",
        AsyncMock(return_value=([{"id": "20"}], [{"id": "30", "type": 99}], [])),
    )
    with pytest.raises(HTTPException) as type_error:
        await routes.push_discord_menu(
            DiscordMenuPushRequest(guild_id="20", channel_id="30"), db, user
        )
    assert type_error.value.status_code == 422
    monkeypatch.setattr(
        routes,
        "_load_discord_options",
        AsyncMock(return_value=([{"id": "20"}], [{"id": "30", "type": 0}], [])),
    )
    monkeypatch.setattr(routes.redis_manager, "hit_rate_limit", AsyncMock(return_value=(False, 4)))
    with pytest.raises(HTTPException) as rate_error:
        await routes.push_discord_menu(
            DiscordMenuPushRequest(guild_id="20", channel_id="30"), db, user
        )
    assert rate_error.value.status_code == 429
