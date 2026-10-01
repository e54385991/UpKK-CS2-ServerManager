"""Discord bot manager mixin: DiscordComponentsMixin."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from modules.models import (
    ManagedPlugin,
)
from modules.schemas.discord import DiscordCapability
from services.compat import LateBoundModule
from services.discord.client import ManagedDiscordClient

if TYPE_CHECKING:
    from services.discord_bot_manager import DiscordBotManager

host = LateBoundModule("services.discord_bot_manager")


async def _menu_open(
    self: DiscordBotManager,
    client: ManagedDiscordClient,
    interaction: discord.Interaction,
    parts: list[str],
) -> None:
    if len(parts) < 4:
        raise host.DiscordAuthorizationDenied("Invalid menu launcher")
    try:
        launcher_issued_at = int(parts[3])
    except ValueError as exc:
        raise host.DiscordAuthorizationDenied("Invalid menu timestamp") from exc
    if host.launcher_is_expired(launcher_issued_at):
        await self._menu_expired_response(interaction)
        return
    view = await self._private_menu_view(client, interaction)
    await interaction.response.send_message(view=view, ephemeral=True)
    return


async def _menu_page(
    self: DiscordBotManager,
    client: ManagedDiscordClient,
    interaction: discord.Interaction,
    parts: list[str],
    issued_at: int,
) -> None:
    page = int(parts[5])
    view = await self._private_menu_view(client, interaction, issued_at=issued_at, page=page)
    await interaction.response.edit_message(view=view)
    return


async def _menu_server(
    self: DiscordBotManager,
    client: ManagedDiscordClient,
    interaction: discord.Interaction,
    parts: list[str],
    issued_at: int,
) -> None:
    values = (interaction.data or {}).get("values") or []
    server_id = int(values[0])
    view = await self._menu_control_view(
        client, interaction, issued_at=issued_at, server_id=server_id
    )
    await interaction.response.edit_message(view=view)
    return


async def _menu_control(
    self: DiscordBotManager,
    client: ManagedDiscordClient,
    interaction: discord.Interaction,
    parts: list[str],
    issued_at: int,
) -> None:
    server_id = int(parts[5])
    view = await self._menu_control_view(
        client, interaction, issued_at=issued_at, server_id=server_id
    )
    await interaction.response.edit_message(view=view)
    return


async def _menu_action(
    self: DiscordBotManager,
    client: ManagedDiscordClient,
    interaction: discord.Interaction,
    parts: list[str],
    issued_at: int,
) -> None:
    server_id = int(parts[5])
    values = (interaction.data or {}).get("values") or []
    if not values:
        raise host.DiscordAuthorizationDenied("No menu action was selected")
    await self._handle_menu_action(
        client,
        interaction,
        issued_at=issued_at,
        server_id=server_id,
        action=str(values[0]),
    )
    return


async def _menu_managed_page(
    self: DiscordBotManager,
    client: ManagedDiscordClient,
    interaction: discord.Interaction,
    parts: list[str],
    issued_at: int,
) -> None:
    kind = parts[2]
    mode = kind.removeprefix("managed_")
    if mode not in {"browse", "upgrade"}:
        raise host.DiscordAuthorizationDenied("Invalid managed plugin mode")
    server_id = int(parts[5])
    page = int(parts[6])
    view = await self._managed_plugin_view(
        client,
        interaction,
        issued_at=issued_at,
        server_id=server_id,
        page=page,
        mode=mode,
    )
    await interaction.response.edit_message(view=view)
    return


async def _menu_managed_pick(
    self: DiscordBotManager,
    client: ManagedDiscordClient,
    interaction: discord.Interaction,
    parts: list[str],
    issued_at: int,
) -> None:
    server_id = int(parts[5])
    mode = parts[6]
    if mode not in {"browse", "upgrade"}:
        raise host.DiscordAuthorizationDenied("Invalid managed plugin mode")
    values = (interaction.data or {}).get("values") or []
    plugin_id = int(values[0])
    capability = (
        DiscordCapability.PLUGIN_UPGRADE if mode == "upgrade" else DiscordCapability.PLUGIN_BROWSE
    )
    server = await self._resolve_menu_server(client, interaction, server_id, capability)
    async with host.async_session_maker() as db:
        plugin = await db.get(ManagedPlugin, plugin_id)
    if plugin is None or plugin.server_id != server.id:
        raise host.DiscordAuthorizationDenied("Managed plugin is unavailable")
    if mode == "browse":
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(embed=self._plugin_embed(plugin), ephemeral=False)
        await host._edit_interaction_message(
            interaction, content=host.menu_text(self._locale(interaction), "published")
        )
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    from services.plugin_auto_update_service import plugin_auto_update_service

    plan = await plugin_auto_update_service.build_plugin_upgrade_plan(server.id, plugin_id)
    if plan["no_op"]:
        await host._publish_interaction_update(
            interaction, content=f"{plan['name']} is already up to date."
        )
        return
    await self._publish_menu_confirmation(
        interaction,
        server,
        "plugin_upgrade",
        DiscordCapability.PLUGIN_UPGRADE,
        {"plugin_id": plugin_id},
        plan,
    )
    return


async def _menu_maps_page(
    self: DiscordBotManager,
    client: ManagedDiscordClient,
    interaction: discord.Interaction,
    parts: list[str],
    issued_at: int,
) -> None:
    kind = parts[2]
    nonce = kind.removeprefix("maps_")
    server_id = int(parts[5])
    page = int(parts[6])
    view = await self._map_picker_view(
        interaction,
        issued_at=issued_at,
        server_id=server_id,
        nonce=nonce,
        page=page,
    )
    await interaction.response.edit_message(view=view)
    return


async def _menu_maps_pick(
    self: DiscordBotManager,
    client: ManagedDiscordClient,
    interaction: discord.Interaction,
    parts: list[str],
    issued_at: int,
) -> None:
    server_id = int(parts[5])
    nonce = parts[6]
    state = await host.redis_manager.get(self._map_state_key(interaction, nonce))
    if not isinstance(state, dict) or state.get("server_id") != server_id:
        raise host.DiscordAuthorizationDenied("Map search state does not match this menu")
    values = (interaction.data or {}).get("values") or []
    identity = str(values[0]) if values else ""
    matches = [
        host.candidate_from_map(item)
        for item in state.get("matches") or []
        if isinstance(item, dict)
    ]
    chosen = next((item for item in matches if item.identity_key == identity), None)
    if chosen is None:
        raise host.DiscordAuthorizationDenied("Selected map is unavailable")
    server = await self._resolve_menu_server(
        client, interaction, server_id, DiscordCapability.CHANGE_MAP
    )
    await interaction.response.defer(ephemeral=True, thinking=True)
    await self._confirm_change_map(interaction, server, chosen, publish="menu")
    await host.redis_manager.delete(self._map_state_key(interaction, nonce))
    return


async def _menu_search_page(
    self: DiscordBotManager,
    client: ManagedDiscordClient,
    interaction: discord.Interaction,
    parts: list[str],
    issued_at: int,
) -> None:
    kind = parts[2]
    nonce = kind.removeprefix("search_")
    server_id = int(parts[5])
    page = int(parts[6])
    view = await self._market_search_view(
        client,
        interaction,
        issued_at=issued_at,
        server_id=server_id,
        nonce=nonce,
        page=page,
    )
    await interaction.response.edit_message(view=view)
    return


async def _menu_market_pick(
    self: DiscordBotManager,
    client: ManagedDiscordClient,
    interaction: discord.Interaction,
    parts: list[str],
    issued_at: int,
) -> None:
    server_id = int(parts[5])
    nonce = parts[6]
    mode = parts[7]
    if mode not in {"browse", "install"}:
        raise host.DiscordAuthorizationDenied("Invalid market plugin mode")
    state = await host.redis_manager.get(self._search_state_key(client, interaction, nonce))
    if (
        not isinstance(state, dict)
        or state.get("server_id") != server_id
        or state.get("mode") != mode
    ):
        raise host.DiscordAuthorizationDenied("Plugin search state does not match this menu")
    values = (interaction.data or {}).get("values") or []
    plugin_id = int(values[0])
    capability = (
        DiscordCapability.PLUGIN_INSTALL if mode == "install" else DiscordCapability.PLUGIN_BROWSE
    )
    server = await self._resolve_menu_server(client, interaction, server_id, capability)
    async with host.async_session_maker() as db:
        plugin = await host.MarketPlugin.get_by_id(db, plugin_id)
    if plugin is None:
        raise host.DiscordAuthorizationDenied("Market plugin is unavailable")
    await interaction.response.defer(ephemeral=True, thinking=True)
    if mode == "browse":
        await interaction.followup.send(embed=self._plugin_embed(plugin), ephemeral=False)
        await host._edit_interaction_message(
            interaction, content=host.menu_text(self._locale(interaction), "published")
        )
        return
    plan, arguments, warnings = await self._market_plan(server, plugin_id)
    await self._publish_menu_confirmation(
        interaction,
        server,
        "plugin_install",
        DiscordCapability.PLUGIN_INSTALL,
        arguments,
        plan,
        warnings=warnings,
    )
    await host.redis_manager.delete(self._search_state_key(client, interaction, nonce))
    return
