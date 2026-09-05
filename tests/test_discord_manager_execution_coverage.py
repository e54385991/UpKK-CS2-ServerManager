"""Cover Discord command execution, confirmation persistence, and operation routing."""

from __future__ import annotations

import hashlib
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.schemas.discord import DiscordCapability
from services import discord_bot_manager as module
from services.discord_bot_manager import DiscordBotManager


class _Db:
    def __init__(self, *, pending=None, server=None, owner=None, saved=None, rows=()):
        self.pending = pending
        self.server = server
        self.owner = owner
        self.saved = saved
        self.rows = list(rows)
        self.added = []
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, model, item_id):
        name = getattr(model, "__name__", "")
        if name == "DiscordOperationRun":
            return self.saved or self.pending
        if name == "Server":
            return self.server
        if name == "User":
            return self.owner
        return None

    async def scalar(self, _query):
        return len(self.rows)

    async def execute(self, _query):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self.rows))

    def add(self, value):
        self.added.append(value)


def _interaction(**overrides):
    values = {
        "guild_id": 10,
        "channel_id": 20,
        "user": SimpleNamespace(id=30, roles=[]),
        "guild": SimpleNamespace(preferred_locale="en-US"),
        "locale": "en-US",
        "response": SimpleNamespace(
            is_done=lambda: False,
            send_message=AsyncMock(),
            defer=AsyncMock(),
            edit_message=AsyncMock(),
        ),
        "followup": SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=77))),
        "edit_original_response": AsyncMock(),
        "original_response": AsyncMock(return_value=SimpleNamespace(id=78)),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _server():
    return SimpleNamespace(id=3, name="demo", user_id=1, game_port=27015, host="example.test")


@pytest.mark.asyncio
async def test_discord_commands_help_status_plugins_and_upgrade(monkeypatch):
    manager = DiscordBotManager()
    client = SimpleNamespace(owner_user_id=1)
    interaction = _interaction()
    server = _server()
    binding = SimpleNamespace(capabilities=[DiscordCapability.STATUS.value])
    db = _Db(rows=[SimpleNamespace(id=4, display_name="Managed", installed_version="1")])
    monkeypatch.setattr(module, "async_session_maker", lambda: db)
    monkeypatch.setattr(module, "authorized_bindings", AsyncMock(return_value=[(binding, server)]))
    await manager.command_help(client, interaction)
    interaction.response.send_message.assert_awaited()

    monkeypatch.setattr(manager, "_resolve_server", AsyncMock(return_value=server))
    monkeypatch.setattr(manager, "_status_embed", AsyncMock(return_value="embed"))
    monkeypatch.setattr(module, "_publish_interaction_update", AsyncMock())
    await manager.command_status(client, interaction, None)
    interaction.response.defer.assert_awaited()

    plugin = SimpleNamespace(id=4, title="Plugin", version="1")
    monkeypatch.setattr(
        module.MarketPlugin, "search_plugins", AsyncMock(return_value=([plugin], 1))
    )
    await manager.command_plugin_search(client, interaction, "plug", None)
    await manager.command_plugin_list(client, interaction, None)
    assert interaction.response.send_message.await_count >= 3

    auto_module = importlib.import_module("services.plugin_auto_update_service")
    plan = {"no_op": True, "name": "Plugin"}
    monkeypatch.setattr(
        auto_module.plugin_auto_update_service,
        "build_plugin_upgrade_plan",
        AsyncMock(return_value=plan),
    )
    await manager.command_plugin_upgrade(client, interaction, 4, None)
    assert "already" in str(module._publish_interaction_update.await_args.kwargs.get("content"))

    plan = {"no_op": False, "name": "Plugin", "steps": []}
    auto_module.plugin_auto_update_service.build_plugin_upgrade_plan.return_value = plan
    monkeypatch.setattr(manager, "_send_confirmation", AsyncMock())
    await manager.command_plugin_upgrade(client, interaction, 4, None)
    manager._send_confirmation.assert_awaited_once()

    monkeypatch.setattr(manager, "_respond_error", AsyncMock())
    monkeypatch.setattr(manager, "_resolve_server", AsyncMock(side_effect=RuntimeError("command")))
    await manager.command_status(client, interaction, None)
    manager._respond_error.assert_awaited_once()
    monkeypatch.setattr(module, "authorized_bindings", AsyncMock(return_value=[]))
    await manager.command_help(client, interaction)
    assert manager._respond_error.await_count == 2


@pytest.mark.asyncio
async def test_confirmation_build_publish_save_and_cancel_paths(monkeypatch):
    manager = DiscordBotManager()
    interaction = _interaction()
    server = _server()
    operation = SimpleNamespace(id="op-1")
    db = _Db(saved=SimpleNamespace(id="op-1", message_id=None))
    monkeypatch.setattr(module, "async_session_maker", lambda: db)
    monkeypatch.setattr(module, "create_operation", AsyncMock(return_value=operation))
    result, embed, view = await manager._build_confirmation(
        interaction, server, "start", DiscordCapability.START, {"action": "start"}, {"steps": []}
    )
    assert result is operation
    assert embed.title == "Confirm start"
    assert view.children

    await manager._send_confirmation(
        interaction, server, "start", DiscordCapability.START, {"action": "start"}, {"steps": []}
    )
    assert db.saved.message_id == "78"
    interaction.edit_original_response.side_effect = (
        module._http_error(10008) if hasattr(module, "_http_error") else None
    )
    interaction.original_response.return_value = SimpleNamespace(id=79)
    await manager._send_confirmation(
        interaction, server, "start", DiscordCapability.START, {"action": "start"}, {"steps": []}
    )

    await manager._publish_menu_confirmation(
        interaction, server, "start", DiscordCapability.START, {"action": "start"}, {"steps": []}
    )
    assert interaction.followup.send.await_count >= 1

    pending = SimpleNamespace(id="op-cancel", status="pending", actor_user_id="30")
    db = _Db(pending=pending)
    monkeypatch.setattr(module, "async_session_maker", lambda: db)
    monkeypatch.setattr(module, "record_discord_operation_event", AsyncMock())
    monkeypatch.setattr(module, "_publish_interaction_update", AsyncMock())
    await manager._cancel_operation(interaction, "op-cancel")
    assert pending.status == "cancelled"
    pending.status = "completed"
    with pytest.raises(module.DiscordOperationDenied):
        await manager._cancel_operation(interaction, "op-cancel")
    pending.status = "pending"
    pending.actor_user_id = "different"
    with pytest.raises(module.DiscordOperationDenied):
        await manager._cancel_operation(interaction, "op-cancel")


@pytest.mark.asyncio
async def test_execute_operation_routes_tools_upgrade_and_ownership(monkeypatch):
    manager = DiscordBotManager()
    server = _server()
    owner = SimpleNamespace(id=1, is_active=True)
    db = _Db(server=server, owner=owner)
    monkeypatch.setattr(module, "async_session_maker", lambda: db)
    handler = AsyncMock(return_value={"success": True, "message": "done"})
    specs = {
        "control_server": SimpleNamespace(handler=handler),
        "run_server_operation": SimpleNamespace(handler=handler),
        "apply_plugin_plan": SimpleNamespace(handler=handler),
        "send_game_console_command": SimpleNamespace(handler=handler),
    }
    monkeypatch.setattr(module, "TOOLS_BY_NAME", specs)
    interaction = _interaction()
    console_command = "status"
    map_command = "changelevel de_dust2"
    for action, arguments in (
        ("start", {}),
        ("stop", {}),
        ("restart", {}),
        ("update", {}),
        ("validate", {}),
        ("plugin_install", {"plugin_id": 4, "expected_plan_hash": "x" * 64}),
        (
            "game_console",
            {
                "command_encrypted": module.encrypt_credential(console_command),
                "command_hash": hashlib.sha256(console_command.encode()).hexdigest(),
            },
        ),
        (
            "change_map",
            {
                "command_encrypted": module.encrypt_credential(map_command),
                "command_hash": hashlib.sha256(map_command.encode()).hexdigest(),
            },
        ),
    ):
        item = SimpleNamespace(
            id=f"{action}-1", action=action, server_id=3, owner_user_id=1, arguments=arguments
        )
        result = await manager._execute_operation(interaction, item)
        assert result["success"] is True
    upgrade = SimpleNamespace(
        id="upgrade",
        action="plugin_upgrade",
        server_id=3,
        owner_user_id=1,
        arguments={"plugin_id": 4},
    )
    enqueue = AsyncMock(return_value={"operation_id": "hub-op"})
    enqueue_module = importlib.import_module("services.operation_enqueue")
    monkeypatch.setattr(enqueue_module, "enqueue_plugin_auto_update", enqueue)
    hub_module = importlib.import_module("services.server_operation_hub")
    monkeypatch.setattr(
        hub_module,
        "server_operation_hub",
        SimpleNamespace(
            wait_until_terminal=AsyncMock(return_value={"success": True, "message": "upgraded"})
        ),
    )
    assert (await manager._execute_operation(interaction, upgrade))["success"] is True
    with pytest.raises(ValueError):
        await manager._execute_operation(
            interaction,
            SimpleNamespace(
                id="unknown", action="unsupported", server_id=3, owner_user_id=1, arguments={}
            ),
        )
    db.server = SimpleNamespace(id=3, user_id=99)
    with pytest.raises(module.DiscordOperationDenied):
        await manager._execute_operation(
            interaction,
            SimpleNamespace(
                id="denied", action="start", server_id=3, owner_user_id=1, arguments={}
            ),
        )


@pytest.mark.asyncio
async def test_confirm_and_execute_success_failure_and_exception(monkeypatch):
    manager = DiscordBotManager()
    server = _server()
    pending = SimpleNamespace(id="op", action="start", server_id=3, owner_user_id=1, arguments={})
    saved = SimpleNamespace(
        id="op",
        action="start",
        status="running",
        result=None,
        error=None,
        server_id=3,
        owner_user_id=1,
        arguments={},
    )
    db = _Db(pending=pending, server=server, saved=saved)
    monkeypatch.setattr(module, "async_session_maker", lambda: db)
    monkeypatch.setattr(manager, "_fresh_plan", AsyncMock(return_value={"steps": []}))
    monkeypatch.setattr(module, "confirm_operation", AsyncMock(return_value=saved))
    monkeypatch.setattr(module, "record_discord_operation_event", AsyncMock())
    publish = AsyncMock()
    monkeypatch.setattr(module, "_publish_interaction_update", publish)
    monkeypatch.setattr(
        manager, "_execute_operation", AsyncMock(return_value={"success": True, "message": "done"})
    )
    await manager._confirm_and_execute(_interaction(), "op")
    assert saved.status == "completed"
    assert publish.await_count == 2

    saved.status = "running"
    manager._execute_operation.side_effect = RuntimeError("boom")
    await manager._confirm_and_execute(_interaction(), "op")
    assert saved.status == "failed"
    assert "boom" in saved.error

    db.pending = None
    db.saved = None
    with pytest.raises(module.DiscordOperationDenied):
        await manager._confirm_and_execute(_interaction(), "missing")
