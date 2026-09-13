"""Managed Discord Gateway client and confirmation views."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import discord
from discord import app_commands

from modules.models import ServerDiscordBinding, UserDiscordBot
from services.compat import LateBoundModule

host = LateBoundModule("services.discord_bot_manager")


class _ConfirmView(discord.ui.View):
    def __init__(self, operation_id: str, *, warnings: bool = False) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Confirm and accept warnings" if warnings else "Confirm",
                style=discord.ButtonStyle.danger,
                custom_id=f"cs2:op:{operation_id}:confirm",
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Cancel",
                style=discord.ButtonStyle.secondary,
                custom_id=f"cs2:op:{operation_id}:cancel",
            )
        )


class _AIConfirmView(discord.ui.View):
    def __init__(self, run_id: str, tool_run_id: str) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Confirm AI operation",
                style=discord.ButtonStyle.danger,
                custom_id=f"cs2:ai:{run_id}:{tool_run_id}:confirm",
            )
        )


class ManagedDiscordClient(discord.Client):
    def __init__(
        self,
        manager: Any,
        owner_user_id: int,
        message_trigger_mode: str = "mention_only",
    ) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = message_trigger_mode == host.MESSAGE_TRIGGER_GREETINGS
        super().__init__(intents=intents)
        self.manager = manager
        self.owner_user_id = owner_user_id
        self.message_trigger_mode = message_trigger_mode
        self.tree = app_commands.CommandTree(self)
        host.register_commands(self)

    async def on_ready(self) -> None:
        await self.manager._client_ready(self)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self.manager._guild_removed(self.owner_user_id, str(guild.id))

    async def on_message(self, message: discord.Message) -> None:
        await self.manager.handle_message(self, message)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = str((interaction.data or {}).get("custom_id") or "")
        if custom_id.startswith(("cs2:op:", "cs2:ai:", "cs2:menu:")):
            await self.manager.handle_component(self, interaction, custom_id)

    async def sync_bound_guilds(self) -> None:
        async with host.async_session_maker() as db:
            result = await db.execute(
                host.select(ServerDiscordBinding).where(
                    ServerDiscordBinding.user_id == self.owner_user_id,
                    host.col(ServerDiscordBinding.enabled).is_(True),
                )
            )
            bindings = list(result.scalars().all())
            bot = await db.get(UserDiscordBot, self.owner_user_id)
        bound_ids = {int(item.guild_id) for item in bindings if item.guild_id}
        if (
            bot is not None
            and bot.global_binding_configured is True
            and bot.global_binding_enabled is True
            and bot.global_guild_id
        ):
            bound_ids.add(int(bot.global_guild_id))
        guild_ids = {guild.id for guild in self.guilds}
        for guild_id in guild_ids:
            guild = discord.Object(id=guild_id)
            try:
                self.tree.clear_commands(guild=guild)
                # Slash / @-app commands are visible in every guild the Bot has
                # joined. Runtime authorization still fail-closes per binding.
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            except Exception as exc:
                host.logger.warning(
                    "Discord command sync failed for owner %s Guild %s: %s",
                    self.owner_user_id,
                    guild_id,
                    exc,
                )
                await self.manager._mark_guild_invalid(
                    self.owner_user_id, str(guild_id), "command_sync_failed"
                )
            else:
                await self.manager._clear_guild_invalid(self.owner_user_id, str(guild_id))
        missing = bound_ids - guild_ids
        for guild_id in missing:
            await self.manager._mark_guild_invalid(
                self.owner_user_id, str(guild_id), "bot_not_in_guild"
            )

    async def _autocomplete_server(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None or interaction.channel_id is None:
            return []
        async with host.async_session_maker() as db:
            try:
                pairs = await host.authorized_bindings(
                    db,
                    bot_owner_user_id=self.owner_user_id,
                    guild_id=str(interaction.guild_id),
                    channel_id=str(interaction.channel_id),
                    actor_user_id=str(interaction.user.id),
                    actor_role_ids=host._roles(interaction),
                    actor_is_channel_manager=host._is_channel_manager(interaction),
                    actor_is_server_administrator=host._is_server_administrator(interaction),
                )
            except host.DiscordAuthorizationDenied:
                return []
        needle = current.casefold().strip()
        return [
            app_commands.Choice(name=server.name[:100], value=str(server.id))
            for _binding, server in pairs
            if not needle or needle in server.name.casefold() or needle in str(server.id)
        ][:25]


@dataclass(slots=True)
class _Runtime:
    client: ManagedDiscordClient
    fingerprint: str
    binding_fingerprint: str
    lease_token: str
    client_task: asyncio.Task
    renew_task: asyncio.Task
