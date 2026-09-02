"""Slash-command registration for the managed Discord client."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import discord
from discord import app_commands

# discord.py's ``CommandTree`` is parameterized by a concrete client type,
# which makes a structural Protocol invariant across the dynamically-created
# managed client.  Command registration runs after all inputs are validated;
# this adapter boundary intentionally accepts the concrete runtime object.
DiscordCommandClient = Any


def _core_handlers(client: DiscordCommandClient) -> dict[str, Callable[..., Any]]:
    async def help_command(interaction: discord.Interaction) -> None:
        await client.manager.command_help(client, interaction)

    async def menu_command(interaction: discord.Interaction) -> None:
        await client.manager.command_menu(client, interaction)

    async def status_command(interaction: discord.Interaction, server: str | None = None) -> None:
        await client.manager.command_status(client, interaction, server)

    async def write_command(
        interaction: discord.Interaction,
        action: str,
        server: str | None,
    ) -> None:
        await client.manager.command_write(client, interaction, action, server)

    async def start_command(interaction: discord.Interaction, server: str | None = None) -> None:
        await write_command(interaction, "start", server)

    async def stop_command(interaction: discord.Interaction, server: str | None = None) -> None:
        await write_command(interaction, "stop", server)

    async def restart_command(interaction: discord.Interaction, server: str | None = None) -> None:
        await write_command(interaction, "restart", server)

    async def update_command(interaction: discord.Interaction, server: str | None = None) -> None:
        await write_command(interaction, "update", server)

    async def validate_command(interaction: discord.Interaction, server: str | None = None) -> None:
        await write_command(interaction, "validate", server)

    async def map_command(
        interaction: discord.Interaction,
        query: str,
        server: str | None = None,
    ) -> None:
        await client.manager.command_change_map(client, interaction, query, server)

    return {
        "help_command": help_command,
        "menu_command": menu_command,
        "status_command": status_command,
        "start_command": start_command,
        "stop_command": stop_command,
        "restart_command": restart_command,
        "update_command": update_command,
        "validate_command": validate_command,
        "map_command": map_command,
    }


def _plugin_handlers(client: DiscordCommandClient) -> dict[str, Callable[..., Any]]:
    async def plugin_search(
        interaction: discord.Interaction,
        query: str,
        server: str | None = None,
    ) -> None:
        await client.manager.command_plugin_search(client, interaction, query, server)

    async def plugin_list(interaction: discord.Interaction, server: str | None = None) -> None:
        await client.manager.command_plugin_list(client, interaction, server)

    async def plugin_install(
        interaction: discord.Interaction,
        plugin_id: int,
        server: str | None = None,
    ) -> None:
        await client.manager.command_plugin_install(client, interaction, plugin_id, server)

    async def plugin_upgrade(
        interaction: discord.Interaction,
        plugin_id: int,
        server: str | None = None,
    ) -> None:
        await client.manager.command_plugin_upgrade(client, interaction, plugin_id, server)

    return {
        "plugin_search": plugin_search,
        "plugin_list": plugin_list,
        "plugin_install": plugin_install,
        "plugin_upgrade": plugin_upgrade,
    }


def _extended_handlers(client: DiscordCommandClient) -> dict[str, Callable[..., Any]]:
    async def console_send(
        interaction: discord.Interaction,
        command: str,
        server: str | None = None,
    ) -> None:
        await client.manager.command_game_console(client, interaction, command, server)

    async def agent_ask(
        interaction: discord.Interaction,
        prompt: str,
        server: str | None = None,
    ) -> None:
        await client.manager.command_agent_ask(client, interaction, prompt, server)

    async def agent_reset(interaction: discord.Interaction, server: str | None = None) -> None:
        await client.manager.command_agent_reset(client, interaction, server)

    return {
        "console_send": console_send,
        "agent_ask": agent_ask,
        "agent_reset": agent_reset,
    }


def register_commands(client: DiscordCommandClient) -> None:
    """Register the stable global command tree against a managed client."""
    cs2 = app_commands.Group(name="cs2", description="Manage authorized CS2 servers")
    plugin = app_commands.Group(name="plugin", description="Browse and manage plugins")
    console = app_commands.Group(name="console", description="Send confirmed game commands")
    agent = app_commands.Group(name="agent", description="Use the server-scoped AI Agent")

    handlers = _core_handlers(client) | _plugin_handlers(client) | _extended_handlers(client)

    commands = [
        cs2.command(name="status", description="Show CS2 server status")(
            handlers["status_command"]
        ),
        cs2.command(name="start", description="Plan and confirm a server start")(
            handlers["start_command"]
        ),
        cs2.command(name="stop", description="Plan and confirm a server stop")(
            handlers["stop_command"]
        ),
        cs2.command(name="restart", description="Plan and confirm a server restart")(
            handlers["restart_command"]
        ),
        cs2.command(name="update", description="Plan and confirm a CS2 update")(
            handlers["update_command"]
        ),
        cs2.command(name="validate", description="Plan and confirm CS2 validation")(
            handlers["validate_command"]
        ),
        cs2.command(name="map", description="Change the current map after confirmation")(
            handlers["map_command"]
        ),
        plugin.command(name="search", description="Search the plugin market")(
            handlers["plugin_search"]
        ),
        plugin.command(name="list", description="List managed plugins")(handlers["plugin_list"]),
        plugin.command(name="install", description="Plan a market plugin install")(
            handlers["plugin_install"]
        ),
        plugin.command(name="upgrade", description="Plan a managed plugin upgrade")(
            handlers["plugin_upgrade"]
        ),
        console.command(name="send", description="Confirm and send one game command")(
            handlers["console_send"]
        ),
        agent.command(name="ask", description="Ask the server-scoped AI Agent")(
            handlers["agent_ask"]
        ),
        agent.command(name="reset", description="Start a new isolated AI context")(
            handlers["agent_reset"]
        ),
    ]
    cs2.command(name="help", description="Show authorized CS2 Bot commands")(
        handlers["help_command"]
    )
    cs2.command(name="menu", description="Open your private CS2 control menu")(
        handlers["menu_command"]
    )
    cs2.add_command(plugin)
    cs2.add_command(console)
    cs2.add_command(agent)
    client.tree.add_command(cs2)
    for command in commands:
        if "server" in {parameter.name for parameter in command.parameters}:
            command.autocomplete("server")(client._autocomplete_server)
