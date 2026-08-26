"""Coverage for lifecycle-owned AI transport and Discord command adapters."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.ai.transport import AIProviderTransport
from services.discord.commands import (
    _core_handlers,
    _extended_handlers,
    _plugin_handlers,
    register_commands,
)


class FakeHTTPClient:
    instances: list["FakeHTTPClient"] = []

    def __init__(self, **_kwargs):
        self.is_closed = False
        self.closed = False
        self.__class__.instances.append(self)

    async def aclose(self):
        self.closed = True
        self.is_closed = True


@pytest.mark.asyncio
async def test_ai_transport_reuses_and_replaces_clients(monkeypatch):
    monkeypatch.setattr("services.ai.transport.httpx.AsyncClient", FakeHTTPClient)
    transport = AIProviderTransport()

    first = await transport._get_client()
    assert await transport._get_client() is first
    await transport.close()
    assert first.closed is True

    second = await transport._get_client()
    assert second is not first
    await transport.close()
    assert second.closed is True


@pytest.mark.asyncio
async def test_discord_command_handlers_delegate_and_register():
    manager = SimpleNamespace()
    for name in (
        "command_help",
        "command_menu",
        "command_status",
        "command_write",
        "command_plugin_search",
        "command_plugin_list",
        "command_plugin_install",
        "command_plugin_upgrade",
        "command_game_console",
        "command_agent_ask",
        "command_agent_reset",
    ):
        setattr(manager, name, AsyncMock())

    async def autocomplete(_interaction, _current):
        return []

    client = SimpleNamespace(manager=manager, _autocomplete_server=autocomplete)

    interaction = SimpleNamespace()
    core = _core_handlers(client)
    await core["help_command"](interaction)
    await core["menu_command"](interaction)
    await core["status_command"](interaction, "1")
    for name in (
        "start_command",
        "stop_command",
        "restart_command",
        "update_command",
        "validate_command",
    ):
        await core[name](interaction, "1")
    plugin = _plugin_handlers(client)
    await plugin["plugin_search"](interaction, "metamod", "1")
    await plugin["plugin_list"](interaction, "1")
    await plugin["plugin_install"](interaction, 7, "1")
    await plugin["plugin_upgrade"](interaction, 7, "1")
    extended = _extended_handlers(client)
    await extended["console_send"](interaction, "status", "1")
    await extended["agent_ask"](interaction, "hello", "1")
    await extended["agent_reset"](interaction, "1")

    class Tree:
        def __init__(self):
            self.commands = []

        def add_command(self, command):
            self.commands.append(command)

    tree = Tree()
    client.tree = tree
    register_commands(client)
    assert [command.name for command in tree.commands] == ["cs2"]
    assert {command.name for command in tree.commands[0].commands} >= {
        "help",
        "menu",
        "plugin",
        "console",
        "agent",
    }
