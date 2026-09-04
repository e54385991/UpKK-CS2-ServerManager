"""Exercise Discord menu components with deterministic local fakes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import discord_bot_manager as module
from services.change_map_service import MapCandidate
from services.discord_bot_manager import DiscordBotManager


class _Db:
    def __init__(self, *, managed=None, market=None):
        self.managed = managed
        self.market = market

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, model, _item_id):
        name = getattr(model, "__name__", "")
        return self.managed if name == "ManagedPlugin" else self.market


def _interaction(**overrides):
    values = {
        "guild_id": 10,
        "channel_id": 20,
        "user": SimpleNamespace(id=30, roles=[]),
        "guild": SimpleNamespace(preferred_locale="en-US"),
        "locale": "en-US",
        "response": SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            defer=AsyncMock(),
            send_modal=AsyncMock(),
        ),
        "followup": SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=88))),
        "data": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _client():
    return SimpleNamespace(owner_user_id=1)


@pytest.mark.asyncio
async def test_menu_component_identity_launcher_paging_and_action_paths(monkeypatch):
    manager = DiscordBotManager()
    client = _client()
    interaction = _interaction()
    monkeypatch.setattr(manager, "_private_menu_view", AsyncMock(return_value="private-view"))
    monkeypatch.setattr(manager, "_menu_expired_response", AsyncMock())
    monkeypatch.setattr(manager, "_handle_menu_action", AsyncMock())
    monkeypatch.setattr(manager, "_menu_control_view", AsyncMock(return_value="control-view"))
    monkeypatch.setattr(module, "launcher_is_expired", lambda _value: False)
    monkeypatch.setattr(module, "menu_is_expired", lambda _value: False)

    with pytest.raises(module.DiscordAuthorizationDenied):
        await manager._handle_menu_component(client, interaction, ["cs2", "menu", "open"])
    with pytest.raises(module.DiscordAuthorizationDenied):
        await manager._handle_menu_component(client, interaction, ["cs2", "menu", "open", "bad"])
    monkeypatch.setattr(module, "launcher_is_expired", lambda _value: True)
    await manager._handle_menu_component(client, interaction, ["cs2", "menu", "open", "1"])
    manager._menu_expired_response.assert_awaited_once()
    monkeypatch.setattr(module, "launcher_is_expired", lambda _value: False)
    await manager._handle_menu_component(client, interaction, ["cs2", "menu", "open", "1"])
    interaction.response.send_message.assert_awaited_once_with(view="private-view", ephemeral=True)

    for invalid in (
        ["cs2", "menu", "page", "bad", "30", "0"],
        ["cs2", "menu", "page", "1", "999", "0"],
    ):
        with pytest.raises(module.DiscordAuthorizationDenied):
            await manager._handle_menu_component(client, interaction, invalid)
    await manager._handle_menu_component(client, interaction, ["cs2", "menu", "page", "1", "30", "2"])
    manager._private_menu_view.assert_awaited()
    interaction.data = {"values": ["3"]}
    await manager._handle_menu_component(client, interaction, ["cs2", "menu", "server", "1", "30"])
    await manager._handle_menu_component(client, interaction, ["cs2", "menu", "control", "1", "30", "3"])
    assert interaction.response.edit_message.await_count >= 3

    interaction.data = {}
    with pytest.raises(module.DiscordAuthorizationDenied):
        await manager._handle_menu_component(
            client, interaction, ["cs2", "menu", "action", "1", "30", "3"]
        )
    interaction.data = {"values": ["status"]}
    await manager._handle_menu_component(
        client, interaction, ["cs2", "menu", "action", "1", "30", "3"]
    )
    manager._handle_menu_action.assert_awaited_once()

    monkeypatch.setattr(module, "menu_is_expired", lambda _value: True)
    await manager._handle_menu_component(
        client, interaction, ["cs2", "menu", "control", "1", "30", "3"]
    )
    assert manager._menu_expired_response.await_count == 2


@pytest.mark.asyncio
async def test_managed_plugin_map_and_market_component_paths(monkeypatch):
    manager = DiscordBotManager()
    client = _client()
    interaction = _interaction()
    monkeypatch.setattr(module, "menu_is_expired", lambda _value: False)
    monkeypatch.setattr(manager, "_managed_plugin_view", AsyncMock(return_value="managed-view"))
    monkeypatch.setattr(manager, "_map_picker_view", AsyncMock(return_value="map-view"))
    monkeypatch.setattr(manager, "_market_search_view", AsyncMock(return_value="search-view"))
    monkeypatch.setattr(manager, "_resolve_menu_server", AsyncMock(return_value=SimpleNamespace(id=3, name="demo")))
    managed = SimpleNamespace(id=7, server_id=3, display_name="Managed", installed_version="1", latest_version="2")
    monkeypatch.setattr(module, "async_session_maker", lambda: _Db(managed=managed))
    for kind in ("managed_browse", "managed_upgrade"):
        await manager._handle_menu_component(
            client, interaction, ["cs2", "menu", kind, "1", "30", "3", "1"]
        )
    interaction.data = {"values": ["7"]}
    monkeypatch.setattr(manager, "_plugin_embed", lambda _plugin: "managed-embed")
    monkeypatch.setattr(module, "_edit_interaction_message", AsyncMock())
    await manager._handle_menu_component(
        client, interaction, ["cs2", "menu", "managed_pick", "1", "30", "3", "browse"]
    )
    interaction.data = {}
    with pytest.raises(module.DiscordAuthorizationDenied):
        await manager._handle_menu_component(
            client, interaction, ["cs2", "menu", "managed_pick", "1", "30", "3", "bad"]
        )

    nonce = "mapnonce"
    candidate = MapCandidate(name="de_dust2", workshop_id=None, filename="de_dust2")
    redis = SimpleNamespace(
        get=AsyncMock(return_value={
            "server_id": 3,
            "query": "dust",
            "matches": [{"name": candidate.name, "filename": candidate.filename, "enabled": True}],
        }),
        delete=AsyncMock(),
    )
    monkeypatch.setattr(manager, "_confirm_change_map", AsyncMock())
    monkeypatch.setattr(module, "redis_manager", redis)
    await manager._handle_menu_component(
        client, interaction, ["cs2", "menu", f"maps_{nonce}", "1", "30", "3", "1"]
    )
    interaction.data = {"values": [candidate.identity_key]}
    await manager._handle_menu_component(
        client, interaction, ["cs2", "menu", "maps_pick", "1", "30", "3", nonce]
    )
    manager._confirm_change_map.assert_awaited_once()
    redis.get.return_value = None
    with pytest.raises(module.DiscordAuthorizationDenied):
        await manager._handle_menu_component(
            client, interaction, ["cs2", "menu", "maps_pick", "1", "30", "3", nonce]
        )

    search_nonce = "searchnonce"
    redis.get.return_value = {"server_id": 3, "query": "plugin", "mode": "browse"}
    await manager._handle_menu_component(
        client, interaction, ["cs2", "menu", f"search_{search_nonce}", "1", "30", "3", "1"]
    )
    market = SimpleNamespace(id=9, server_id=3, title="Market", version="1", author="Author")
    monkeypatch.setattr(module, "async_session_maker", lambda: _Db(market=market))
    monkeypatch.setattr(module.MarketPlugin, "get_by_id", AsyncMock(return_value=market))
    interaction.data = {"values": ["9"]}
    monkeypatch.setattr(manager, "_plugin_embed", lambda _plugin: "market-embed")
    await manager._handle_menu_component(
        client, interaction, ["cs2", "menu", "market_pick", "1", "30", "3", search_nonce, "browse"]
    )
    redis.get.return_value = {"server_id": 3, "query": "plugin", "mode": "install"}
    monkeypatch.setattr(manager, "_market_plan", AsyncMock(return_value=({}, {}, False)))
    monkeypatch.setattr(manager, "_publish_menu_confirmation", AsyncMock())
    await manager._handle_menu_component(
        client, interaction, ["cs2", "menu", "market_pick", "1", "30", "3", search_nonce, "install"]
    )
    manager._publish_menu_confirmation.assert_awaited_once()


@pytest.mark.asyncio
async def test_menu_submit_helpers_and_ai_component_progress(monkeypatch):
    manager = DiscordBotManager()
    client = _client()
    interaction = _interaction()
    server = SimpleNamespace(id=3, name="demo", game_port=27015)
    monkeypatch.setattr(manager, "_resolve_menu_server", AsyncMock(return_value=server))
    redis = SimpleNamespace(set=AsyncMock(return_value=True), get=AsyncMock(return_value={"server_id": 3, "query": "x", "mode": "browse"}), delete=AsyncMock())
    monkeypatch.setattr(module, "redis_manager", redis)
    monkeypatch.setattr(manager, "_market_search_view", AsyncMock(return_value="view"))
    monkeypatch.setattr(module, "_edit_interaction_message", AsyncMock())
    await manager._menu_search_submit(client, interaction, issued_at=1, server_id=3, mode="browse", query=" x ")
    with pytest.raises(module.DiscordAuthorizationDenied):
        await manager._menu_search_submit(client, interaction, issued_at=1, server_id=3, mode="bad", query="x")
    redis.set.return_value = False
    with pytest.raises(module.DiscordAuthorizationDenied):
        await manager._menu_search_submit(client, interaction, issued_at=1, server_id=3, mode="browse", query="x")

    monkeypatch.setattr(manager, "_publish_menu_confirmation", AsyncMock())
    await manager._menu_console_submit(client, interaction, server_id=3, command="status")
    candidate = MapCandidate(name="de", workshop_id=None, filename="de")
    monkeypatch.setattr(manager, "_start_change_map", AsyncMock())
    await manager._menu_map_submit(client, interaction, issued_at=1, server_id=3, query="de")
    monkeypatch.setattr(module, "ask_discord_agent", AsyncMock(return_value="run-1"))
    monkeypatch.setattr(manager, "_render_ai_run_message", AsyncMock(return_value=True))
    await manager._menu_agent_submit(client, interaction, server_id=3, prompt="hello")

    monkeypatch.setattr(module, "approve_discord_tool", AsyncMock(return_value={"success": True}))
    monkeypatch.setattr(module, "discord_run_snapshot", AsyncMock(return_value={"progress": {"status": "done"}}))
    monkeypatch.setattr(manager, "_render_ai_run", AsyncMock())
    await manager.handle_component(
        client,
        interaction,
        "cs2:ai:run-1:tool-1",
    )
    assert manager._render_ai_run.await_count == 1
