"""Cover Discord command dispatch and menu helpers without a Gateway connection."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from modules import ManagedPlugin, MarketPlugin
from modules.schemas.discord import DiscordCapability
from services import discord_bot_manager as module
from services.change_map_service import ChangeMapAmbiguousError, MapCandidate
from services.discord_bot_manager import DiscordBotManager, ManagedDiscordClient
from services.discord_bot_service import DiscordBotAPIError


class _Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


def _interaction(**overrides):
    values = dict(
        guild_id=10,
        channel_id=20,
        user=SimpleNamespace(id=30, roles=[]),
        guild=SimpleNamespace(preferred_locale="en-US"),
        locale="en-US",
        response=SimpleNamespace(
            is_done=lambda: False,
            send_message=AsyncMock(),
            defer=AsyncMock(),
            edit_message=AsyncMock(),
            send_modal=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        data={},
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _server(**overrides):
    values = {"id": 3, "name": "demo", "game_port": 27015, "host": "example.test"}
    values.update(overrides)
    return SimpleNamespace(**values)


def _http_error(code=10008):
    return discord.HTTPException(
        SimpleNamespace(status=404, reason="Not Found"), {"code": code, "message": "gone"}
    )


@pytest.mark.asyncio
async def test_message_edit_publish_and_public_error_fallbacks(monkeypatch):
    no_edit = SimpleNamespace()
    assert await module._edit_webhook_message(no_edit, content="x") is False
    sync_edit = SimpleNamespace(edit=lambda **_kwargs: None)
    assert await module._edit_webhook_message(sync_edit, content="x") is True
    gone = SimpleNamespace(edit=AsyncMock(side_effect=_http_error()))
    assert await module._edit_webhook_message(gone, content="x") is False
    rejected = SimpleNamespace(edit=AsyncMock(side_effect=_http_error(50001)))
    with pytest.raises(discord.HTTPException):
        await module._edit_webhook_message(rejected, content="x")

    interaction = _interaction()
    interaction.response.edit_message = AsyncMock(side_effect=_http_error())
    interaction.response.is_done = lambda: False
    assert await module._publish_interaction_update(interaction, content="published", view=None)
    interaction = _interaction()
    interaction.response.edit_message = AsyncMock(side_effect=_http_error())
    interaction.response.send_message = AsyncMock(side_effect=_http_error(50001))
    assert not await module._publish_interaction_update(interaction, content="published")
    interaction = _interaction()
    interaction.response.edit_message = AsyncMock(side_effect=_http_error())
    assert not await module._publish_interaction_update(interaction, view=None, content=None)

    permissions = SimpleNamespace(administrator=False, manage_channels=False)
    source = SimpleNamespace(
        guild=SimpleNamespace(owner_id=99),
        user=SimpleNamespace(id=30),
        permissions=permissions,
        channel=SimpleNamespace(permissions_for=lambda _member: (_ for _ in ()).throw(TypeError())),
    )
    assert module._actor_privileges(source) == (False, False)
    assert module._is_server_administrator(source) is False
    assert module._is_channel_manager(source) is False


def test_status_formats_and_view_constructors_cover_remaining_helpers(monkeypatch):
    monkeypatch.setattr(module, "get_current_time", lambda: __import__("datetime").datetime(2025, 1, 1))
    assert module.format_panel_update_age("2025-01-01T00:00:05") == "2025-01-01 00:00:05"
    assert module.format_panel_update_age("2024-12-31T23:59:59") == "1s ago"
    assert module.format_panel_update_age("2024-12-31T23:00:00") == "1h ago"
    assert module.format_panel_update_age("2024-12-30T00:00:00") == "2024-12-30 00:00:00"
    assert module._real_status_text(None, "en-US") == module._status_unknown("en-US")
    assert module._real_status_text(" ", "en-US") == module._status_unknown("en-US")
    assert module._format_disk_percent("bad", "en-US") == module._status_unknown("en-US")
    fields = module.status_card_fields(
        _server(),
        a2s_ok=True,
        info={"player_count": 2, "max_players": 10, "server_name": "live", "map_name": "de", "version": "1"},
        response_time_ms=12,
        last_updated="2025-01-01T00:00:00",
        disk_info={"used_gb": 1, "total_gb": 2, "used_percent": 50},
    )
    assert fields[3][1] == "2/10"
    assert module._ConfirmView("op", warnings=True).children
    assert module._AIConfirmView("run", "tool").children
    candidate = MapCandidate(name="map", workshop_id="123", filename="map.bsp")
    item = SimpleNamespace(
        arguments={
            "command_encrypted": module.encrypt_credential(candidate.command),
            "command_hash": hashlib.sha256(candidate.command.encode()).hexdigest(),
            "name": "different",
            "workshop_id": "456",
            "filename": "map.bsp",
        }
    )
    with pytest.raises(module.DiscordOperationDenied, match="changed"):
        module._operation_change_map_candidate(item)


@pytest.mark.asyncio
async def test_managed_client_callbacks_autocomplete_and_authorized_pairs(monkeypatch):
    manager = SimpleNamespace(
        _client_ready=AsyncMock(),
        _guild_removed=AsyncMock(),
        handle_message=AsyncMock(),
        handle_component=AsyncMock(),
        _mark_guild_invalid=AsyncMock(),
        _clear_guild_invalid=AsyncMock(),
    )
    monkeypatch.setattr(module, "register_commands", lambda _client: None)
    client = ManagedDiscordClient(manager, 4)
    await client.on_ready()
    await client.on_guild_remove(SimpleNamespace(id=12))
    await client.on_message(SimpleNamespace())
    await client.on_interaction(SimpleNamespace(type=discord.InteractionType.application_command, data={}))
    await client.on_interaction(
        SimpleNamespace(type=discord.InteractionType.component, data={"custom_id": "cs2:menu:open"})
    )
    manager.handle_component.assert_awaited_once()

    interaction = _interaction(guild_id=None)
    assert await client._autocomplete_server(interaction, "x") == []
    server = _server(name="Alpha")
    binding = SimpleNamespace(capabilities=["status"])
    class _Context:
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def execute(self, _query): return _Result([(binding, server)])
    monkeypatch.setattr(module, "async_session_maker", lambda: _Context())
    monkeypatch.setattr(module, "authorized_bindings", AsyncMock(return_value=[(binding, server)]))
    interaction = _interaction()
    interaction.user.roles = [SimpleNamespace(id=7)]
    choices = await client._autocomplete_server(interaction, "alp")
    assert choices[0].value == "3"
    monkeypatch.setattr(module, "authorized_bindings", AsyncMock(side_effect=module.DiscordAuthorizationDenied("no")))
    assert await client._autocomplete_server(interaction, "") == []

    manager_obj = DiscordBotManager()
    manager_obj._update_bot_status = AsyncMock()
    manager_obj._runtimes[4] = SimpleNamespace(client=SimpleNamespace(is_ready=lambda: False))
    assert manager_obj.configuration_options(4) is None
    monkeypatch.setattr(module, "async_session_maker", lambda: _Context())
    monkeypatch.setattr(module, "authorized_bindings", AsyncMock(return_value=[(binding, server)]))
    client_stub = SimpleNamespace(owner_user_id=4)
    assert await manager_obj._authorized_menu_pairs(client_stub, guild_id=None, channel_id=20, actor_user_id=1, actor_role_ids=set(), actor_is_channel_manager=False) == []
    pairs = await manager_obj._authorized_menu_pairs(client_stub, guild_id=10, channel_id=20, actor_user_id=1, actor_role_ids=set(), actor_is_channel_manager=False)
    assert pairs == [(binding, server)]


@pytest.mark.asyncio
async def test_command_wrappers_build_confirmations_and_error_replies(monkeypatch):
    manager = DiscordBotManager()
    client = SimpleNamespace(owner_user_id=1)
    server = _server()
    interaction = _interaction()
    monkeypatch.setattr(manager, "_resolve_server", AsyncMock(return_value=server))
    monkeypatch.setattr(manager, "_send_confirmation", AsyncMock())
    monkeypatch.setattr(manager, "_respond_error", AsyncMock())
    monkeypatch.setattr(manager, "_market_plan", AsyncMock(return_value=({}, {}, False)))
    monkeypatch.setattr(module, "_publish_interaction_update", AsyncMock(return_value=True))
    await manager.command_write(client, interaction, "start", None)
    await manager.command_game_console(client, interaction, "status", None)
    await manager.command_plugin_install(client, interaction, 5, None)
    monkeypatch.setattr(manager, "_start_change_map", AsyncMock())
    await manager.command_change_map(client, interaction, "de", None)
    monkeypatch.setattr(manager, "_resolve_server", AsyncMock(side_effect=ValueError("bad")))
    await manager.command_write(client, interaction, "start", None)
    assert manager._respond_error.await_count == 1

    monkeypatch.setattr(manager, "_resolve_server", AsyncMock(return_value=server))
    monkeypatch.setattr(module, "ask_discord_agent", AsyncMock(return_value="run"))
    monkeypatch.setattr(manager, "_render_ai_run", AsyncMock())
    await manager.command_agent_ask(client, interaction, "hello", None)
    monkeypatch.setattr(module, "reset_discord_conversation", AsyncMock())
    await manager.command_agent_reset(client, interaction, None)


@pytest.mark.asyncio
async def test_change_map_and_message_agent_failure_choices(monkeypatch):
    manager = DiscordBotManager()
    server = _server()
    interaction = _interaction()
    candidate = MapCandidate(name="de", workshop_id="", filename="de_dust2")
    monkeypatch.setattr(module, "load_map_pool", AsyncMock(return_value=[]))
    with pytest.raises(module.ChangeMapError):
        await manager._start_change_map(interaction, server, "missing", publish="slash")
    monkeypatch.setattr(module, "resolve_change_map", lambda *_args: (None, [candidate]))
    with pytest.raises(ChangeMapAmbiguousError):
        await manager._start_change_map(interaction, server, "de", publish="slash")
    monkeypatch.setattr(module, "resolve_change_map", lambda *_args: (candidate, []))
    manager._send_confirmation = AsyncMock()
    await manager._start_change_map(interaction, server, "de", publish="slash")
    assert manager._send_confirmation.await_count == 1

    client = SimpleNamespace(owner_user_id=1)
    binding = SimpleNamespace(capabilities=[DiscordCapability.AGENT_ASK.value])
    monkeypatch.setattr(module, "available_discord_agent_server_ids", AsyncMock(return_value={3}))
    monkeypatch.setattr(module, "resolve_discord_agent_server", lambda _prompt, servers: next(servers, None))
    assert await manager._message_agent_server(client, [(binding, server)], "hello") == server
    message = SimpleNamespace(guild=None, reply=AsyncMock())
    await manager._run_message_agent(client, message, server, "x")
    message.guild = SimpleNamespace(id=10)
    message.author = SimpleNamespace(id=30)
    message.channel = SimpleNamespace(id=20)
    progress = SimpleNamespace(edit=AsyncMock())
    message.reply = AsyncMock(return_value=progress)
    monkeypatch.setattr(module, "ask_discord_agent", AsyncMock(side_effect=RuntimeError("agent bad")))
    await manager._run_message_agent(client, message, server, "x")
    progress.edit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        "plugin_search",
        "plugin_install",
        "game_console",
        "change_map",
        "agent_ask",
        "plugin_list",
        "plugin_upgrade",
        "status",
        "start",
        "stop",
        "restart",
        "update",
        "validate",
        "agent_reset",
    ],
)
async def test_handle_menu_action_dispatches_each_public_action(monkeypatch, action):
    manager = DiscordBotManager()
    client = SimpleNamespace(owner_user_id=1)
    interaction = _interaction()
    server = _server()
    monkeypatch.setattr(manager, "_resolve_menu_server", AsyncMock(return_value=server))
    monkeypatch.setattr(manager, "_managed_plugin_view", AsyncMock(return_value=object()))
    monkeypatch.setattr(manager, "_publish_menu_status", AsyncMock())
    monkeypatch.setattr(manager, "_publish_menu_confirmation", AsyncMock())
    monkeypatch.setattr(module, "reset_discord_conversation", AsyncMock())
    await manager._handle_menu_action(
        client, interaction, issued_at=1, server_id=3, action=action
    )
    if action in {"plugin_search", "plugin_install", "game_console", "change_map", "agent_ask"}:
        interaction.response.send_modal.assert_awaited_once()
    elif action in {"plugin_list", "plugin_upgrade"}:
        interaction.response.edit_message.assert_awaited_once()
    elif action == "status":
        manager._publish_menu_status.assert_awaited_once()
    elif action == "agent_reset":
        module.reset_discord_conversation.assert_awaited_once()
    else:
        manager._publish_menu_confirmation.assert_awaited_once()

    with pytest.raises(module.DiscordAuthorizationDenied):
        await manager._handle_menu_action(
            client, interaction, issued_at=1, server_id=3, action="not-an-action"
        )


@pytest.mark.asyncio
async def test_menu_component_open_expired_identity_and_unknown_branches(monkeypatch):
    manager = DiscordBotManager()
    client = SimpleNamespace(owner_user_id=1)
    interaction = _interaction()
    monkeypatch.setattr(module, "launcher_is_expired", lambda _value: True)
    await manager._handle_menu_component(client, interaction, ["cs2", "menu", "open", "1"])
    interaction.response.send_message.assert_awaited_once()
    with pytest.raises(module.DiscordAuthorizationDenied, match="timestamp"):
        await manager._handle_menu_component(client, interaction, ["cs2", "menu", "open", "bad"])
    with pytest.raises(module.DiscordAuthorizationDenied, match="component"):
        await manager._handle_menu_component(client, interaction, ["cs2", "menu"])
    with pytest.raises(module.DiscordAuthorizationDenied, match="identity"):
        await manager._handle_menu_component(client, interaction, ["cs2", "menu", "page", "bad", "30"])
    monkeypatch.setattr(module, "menu_is_expired", lambda _value: True)
    await manager._handle_menu_component(client, interaction, ["cs2", "menu", "page", "1", "30", "0"])
    monkeypatch.setattr(module, "menu_is_expired", lambda _value: False)
    with pytest.raises(module.DiscordAuthorizationDenied, match="Unknown menu"):
        await manager._handle_menu_component(client, interaction, ["cs2", "menu", "wat", "1", "30"])


@pytest.mark.asyncio
async def test_market_map_views_and_rendered_ai_states(monkeypatch):
    manager = DiscordBotManager()
    client = SimpleNamespace(owner_user_id=1)
    interaction = _interaction()
    server = _server()
    manager._resolve_menu_server = AsyncMock(return_value=server)
    state = {"query": "test", "mode": "browse", "server_id": 3}
    monkeypatch.setattr(module.redis_manager, "get", AsyncMock(return_value=state))
    monkeypatch.setattr(module.MarketPlugin, "search_plugins", AsyncMock(return_value=([], 0)))
    view = await manager._market_search_view(client, interaction, issued_at=1, server_id=3, nonce="n", page=0)
    assert view is not None
    monkeypatch.setattr(module.redis_manager, "get", AsyncMock(return_value=None))
    with pytest.raises(module.DiscordAuthorizationDenied, match="expired"):
        await manager._market_search_view(client, interaction, issued_at=1, server_id=3, nonce="n", page=0)
    monkeypatch.setattr(module.redis_manager, "get", AsyncMock(return_value={"mode": "bad", "server_id": 3}))
    with pytest.raises(module.DiscordAuthorizationDenied, match="does not match"):
        await manager._market_search_view(client, interaction, issued_at=1, server_id=3, nonce="n", page=0)

    message = SimpleNamespace(edit=AsyncMock())
    monkeypatch.setattr(module, "discord_run_snapshot", AsyncMock(return_value={"status": "completed", "message": "done", "error": None, "tool": None}))
    assert await manager._render_ai_run_message(message, "run")
    monkeypatch.setattr(module, "discord_run_snapshot", AsyncMock(return_value={"status": "waiting_approval", "tool": {"name": "start", "id": "t", "plan": {}}}))
    assert await manager._render_ai_run_message(message, "run")
    interaction = _interaction()
    await manager._render_ai_run(interaction, "run")
    assert interaction.response.edit_message.await_count >= 1
