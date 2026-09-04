"""Cover Discord Gateway runtime/menu orchestration with local fakes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import discord_bot_manager as discord_manager
from services.discord_bot_manager import DiscordBotManager
from services.discord_authorization_service import DiscordAuthorizationDenied


class _Context:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return None


class _Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _Db:
    def __init__(self, rows=()):
        self.rows = rows
        self.added = []
        self.commits = 0

    async def execute(self, _query):
        return _Result(self.rows)

    async def get(self, _model, _id):
        return None

    async def commit(self):
        self.commits += 1

    def add(self, value):
        self.added.append(value)


def _server(**overrides):
    values = dict(id=3, name="demo", host="example.test", game_port=27015, game_directory="/srv/cs2")
    values.update(overrides)
    return SimpleNamespace(**values)


def _interaction(**overrides):
    values = dict(
        guild_id=10,
        channel_id=20,
        user=SimpleNamespace(id=30, roles=[]),
        guild=SimpleNamespace(preferred_locale="en-US"),
        response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        data={},
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_discord_configuration_options_filters_channels_and_missing_guild():
    manager = DiscordBotManager()
    channel = SimpleNamespace(
        id=1,
        name="general",
        type=SimpleNamespace(value=0),
        permissions_for=lambda _member: SimpleNamespace(
            view_channel=True, send_messages=True, embed_links=True, read_message_history=True
        ),
    )
    hidden = SimpleNamespace(
        id=2,
        name="hidden",
        type=SimpleNamespace(value=0),
        permissions_for=lambda _member: SimpleNamespace(
            view_channel=False, send_messages=False, embed_links=False, read_message_history=False
        ),
    )
    guild = SimpleNamespace(
        id=10,
        name="Guild",
        icon=None,
        me=object(),
        channels=[channel, hidden],
        threads=[channel],
        roles=[SimpleNamespace(id=4, name="Admin", position=2)],
    )
    client = SimpleNamespace(
        is_ready=lambda: True,
        guilds=[guild],
        get_guild=lambda guild_id: guild if guild_id == 10 else None,
    )
    manager._runtimes[1] = SimpleNamespace(client=client)
    snapshot = manager.configuration_options(1, "10")
    assert [item["id"] for item in snapshot["channels"]] == ["1"]
    assert snapshot["roles"][0]["name"] == "Admin"
    assert manager.configuration_options(1, "99")["guild_missing"] is True
    assert manager.configuration_options(2) is None


@pytest.mark.asyncio
async def test_discord_server_resolution_and_error_response_cover_authorization_states(monkeypatch):
    manager = DiscordBotManager()
    db = _Db()
    monkeypatch.setattr(discord_manager, "async_session_maker", lambda: _Context(db))
    server = _server()
    binding = SimpleNamespace(capabilities=["status"])
    monkeypatch.setattr(discord_manager, "authorized_bindings", AsyncMock(return_value=[(binding, server)]))
    client = SimpleNamespace(owner_user_id=1)
    interaction = _interaction()
    resolved = await manager._resolve_server(client, interaction, discord_manager.DiscordCapability.STATUS, None)
    assert resolved.id == 3
    assert await manager._resolve_server(client, interaction, discord_manager.DiscordCapability.STATUS, "3") == server
    with pytest.raises(DiscordAuthorizationDenied, match="unavailable"):
        await manager._resolve_server(client, interaction, discord_manager.DiscordCapability.STATUS, "bad")

    monkeypatch.setattr(discord_manager, "authorized_bindings", AsyncMock(return_value=[]))
    with pytest.raises(DiscordAuthorizationDenied, match="No authorized"):
        await manager._resolve_server(client, interaction, discord_manager.DiscordCapability.STATUS, None)
    monkeypatch.setattr(discord_manager, "authorized_bindings", AsyncMock(return_value=[(binding, server), (binding, _server(id=4))]))
    with pytest.raises(DiscordAuthorizationDenied, match="Multiple"):
        await manager._resolve_server(client, interaction, discord_manager.DiscordCapability.STATUS, None)

    response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
    await manager._respond_error(_interaction(response=response), ValueError("bad input"))
    response = SimpleNamespace(is_done=lambda: True, send_message=AsyncMock())
    followup = SimpleNamespace(send=AsyncMock())
    await manager._respond_error(_interaction(response=response, followup=followup), ValueError("bad input"))
    response.send_message.assert_not_awaited()
    followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_discord_menu_sync_and_status_embed_cover_success_and_failure(monkeypatch):
    manager = DiscordBotManager()
    db = _Db([SimpleNamespace(guild_id="10")])
    bot = SimpleNamespace(global_binding_configured=True, global_binding_enabled=True, global_guild_id="11")

    async def get(_model, _id):
        return bot

    db.get = get
    monkeypatch.setattr(discord_manager, "async_session_maker", lambda: _Context(db))
    tree = SimpleNamespace(
        clear_commands=lambda **_k: None,
        copy_global_to=lambda **_k: None,
        sync=AsyncMock(side_effect=[None, RuntimeError("sync")]),
    )
    client = SimpleNamespace(
        owner_user_id=1,
        guilds=[SimpleNamespace(id=10)],
        tree=tree,
        manager=manager,
    )
    manager._mark_guild_invalid = AsyncMock()
    manager._clear_guild_invalid = AsyncMock()
    await discord_manager.ManagedDiscordClient.sync_bound_guilds(client)
    assert manager._clear_guild_invalid.await_count == 1
    assert manager._mark_guild_invalid.await_count == 1

    monkeypatch.setattr(
        discord_manager,
        "load_panel_status_sources",
        AsyncMock(return_value={"a2s_ok": True, "info": {"server_name": "Live"}, "response_time_ms": 5, "last_updated": None, "disk_info": {}}),
    )
    embed = await manager._status_embed(_server())
    assert embed.title == "Live"


def test_discord_menu_views_and_plugin_embeds_cover_empty_single_and_multiple():
    manager = DiscordBotManager()
    empty = manager._menu_view_for_pairs("en-US", [], requester_user_id=1)
    assert empty is not None
    binding = SimpleNamespace(capabilities=["status", "start"])
    one = manager._menu_view_for_pairs("en-US", [(binding, _server())], requester_user_id=1)
    two = manager._menu_view_for_pairs(
        "en-US", [(binding, _server()), (binding, _server(id=4, name="other"))], requester_user_id=1
    )
    assert one is not None and two is not None
    from modules import MarketPlugin, ManagedPlugin

    market = MarketPlugin(id=1, title="Market", github_url="https://github.com/a/b")
    managed = ManagedPlugin(
        id=2,
        server_id=3,
        source_type="market",
        source_key="1",
        display_name="Managed",
        installed_version="1.0",
    )
    assert manager._plugin_embed(market).title == "Market"
    assert manager._plugin_embed(managed).title == "Managed"
