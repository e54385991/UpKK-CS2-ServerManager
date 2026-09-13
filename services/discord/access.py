"""Discord bot manager mixin: DiscordAccessMixin."""

from __future__ import annotations

from contextlib import suppress

import discord

from modules.models import (
    Server,
    ServerDiscordBinding,
)
from modules.schemas.discord import DiscordCapability
from services.compat import LateBoundModule
from services.discord.client import ManagedDiscordClient
from services.discord.types import Manager

host = LateBoundModule("services.discord_bot_manager")


class DiscordAccessMixin:
    @staticmethod
    def _locale(source: discord.Interaction | discord.Message) -> object:
        locale = getattr(source, "locale", None)
        guild = getattr(source, "guild", None)
        return locale or getattr(guild, "preferred_locale", None) or "en-US"

    async def _authorized_menu_pairs(
        self: Manager,
        client: ManagedDiscordClient,
        *,
        guild_id: int | None,
        channel_id: int | None,
        actor_user_id: int,
        actor_role_ids: set[str],
        actor_is_channel_manager: bool,
        actor_is_server_administrator: bool = False,
        required_capability: DiscordCapability | None = None,
    ) -> list[tuple[ServerDiscordBinding, Server]]:
        if guild_id is None or channel_id is None:
            return []
        async with host.async_session_maker() as db:
            pairs = await host.authorized_bindings(
                db,
                bot_owner_user_id=client.owner_user_id,
                guild_id=str(guild_id),
                channel_id=str(channel_id),
                actor_user_id=str(actor_user_id),
                actor_role_ids=actor_role_ids,
                actor_is_channel_manager=actor_is_channel_manager,
                actor_is_server_administrator=actor_is_server_administrator,
                required_capability=required_capability,
            )
        if required_capability is not None:
            return pairs
        known = {item.value for item in DiscordCapability}
        return [
            (binding, server)
            for binding, server in pairs
            if known & set(binding.capabilities or [])
        ]

    async def handle_message(
        self: Manager, client: ManagedDiscordClient, message: discord.Message
    ) -> None:
        """Publish a requester-bound menu for authorized leading-mention messages."""
        if (
            message.guild is None
            or message.webhook_id is not None
            or getattr(message.author, "bot", False)
            or client.user is None
        ):
            return
        mentioned = host._message_mentions_bot(message, client.user.id)
        mention_content = host.mention_trigger_content(
            getattr(message, "content", None) or "",
            client.user.id,
            mentioned=mentioned,
        )
        exact_wake_word = host.is_exact_wake_word(message.content, client.user.id)
        mention_trigger = mention_content is not None
        greeting = client.message_trigger_mode == host.MESSAGE_TRIGGER_GREETINGS and exact_wake_word
        if not mention_trigger and not greeting:
            return
        try:
            pairs = await self._authorized_menu_pairs(
                client,
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                actor_user_id=message.author.id,
                actor_role_ids=host._member_roles(message.author),
                actor_is_channel_manager=host._is_channel_manager(message),
                actor_is_server_administrator=host._is_server_administrator(message),
            )
            if not pairs:
                return
            allowed, _retry_after = await host.redis_manager.hit_rate_limit(
                (
                    f"discord_menu_trigger:{client.owner_user_id}:{message.guild.id}:"
                    f"{message.channel.id}:{message.author.id}"
                ),
                1,
                5,
            )
            if not allowed:
                return
            if mention_content and not exact_wake_word:
                agent_server = await self._message_agent_server(client, pairs, mention_content)
                if agent_server is not None:
                    await self._run_message_agent(client, message, agent_server, mention_content)
                    return
            await message.reply(
                view=self._menu_view_for_pairs(
                    self._locale(message),
                    pairs,
                    requester_user_id=message.author.id,
                ),
                allowed_mentions=discord.AllowedMentions.none(),
                mention_author=False,
                delete_after=host.MENU_LIFETIME_SECONDS,
                silent=True,
            )
        except discord.HTTPException as exc:
            if host._is_unknown_discord_resource(exc):
                host.logger.info(
                    "Discord menu skipped; trigger message is gone for owner %s",
                    client.owner_user_id,
                )
                return
            host.logger.exception(
                "Discord friendly-menu trigger failed for owner %s Guild %s channel %s",
                client.owner_user_id,
                message.guild.id,
                message.channel.id,
            )
        except Exception:
            host.logger.exception(
                "Discord friendly-menu trigger failed for owner %s Guild %s channel %s",
                client.owner_user_id,
                message.guild.id,
                message.channel.id,
            )

    async def _message_agent_server(
        self: Manager,
        client: ManagedDiscordClient,
        pairs: list[tuple[ServerDiscordBinding, Server]],
        prompt: str,
    ) -> Server | None:
        agent_servers = [
            server
            for binding, server in pairs
            if server.id is not None
            and DiscordCapability.AGENT_ASK.value in set(binding.capabilities or [])
        ]
        available_ids = await host.available_discord_agent_server_ids(
            owner_user_id=client.owner_user_id,
            server_ids=(server.id for server in agent_servers if server.id is not None),
        )
        return host.resolve_discord_agent_server(
            prompt,
            (server for server in agent_servers if server.id in available_ids),
        )

    async def _run_message_agent(
        self: Manager,
        client: ManagedDiscordClient,
        message: discord.Message,
        server: Server,
        prompt: str,
    ) -> None:
        if message.guild is None:
            return
        progress_message = await message.reply(
            embed=discord.Embed(
                title=f"AI Agent · {server.name}",
                description="Working…",
                color=discord.Color.blurple(),
            ),
            allowed_mentions=discord.AllowedMentions.none(),
            mention_author=False,
            silent=True,
        )
        try:
            run_id = await host.ask_discord_agent(
                owner_user_id=client.owner_user_id,
                server_id=server.id,
                actor_user_id=str(message.author.id),
                guild_id=str(message.guild.id),
                channel_id=str(message.channel.id),
                prompt=prompt,
            )
            await self._render_ai_run_message(progress_message, run_id)
        except Exception as exc:
            await host._edit_webhook_message(
                progress_message,
                embed=discord.Embed(
                    title=f"AI Agent · {server.name}",
                    description=host._public_error_text(exc),
                    color=discord.Color.red(),
                ),
                view=None,
            )

    async def _menu_pairs_for_interaction(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        required_capability: DiscordCapability | None = None,
    ) -> list[tuple[ServerDiscordBinding, Server]]:
        return await self._authorized_menu_pairs(
            client,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            actor_user_id=interaction.user.id,
            actor_role_ids=host._roles(interaction),
            actor_is_channel_manager=host._is_channel_manager(interaction),
            actor_is_server_administrator=host._is_server_administrator(interaction),
            required_capability=required_capability,
        )

    @staticmethod
    def _menu_server_payload(
        pairs: list[tuple[ServerDiscordBinding, Server]],
    ) -> list[dict]:
        known = {item.value for item in DiscordCapability}
        return [
            {
                "id": server.id,
                "name": server.name,
                "capability_count": len(known & set(binding.capabilities or [])),
            }
            for binding, server in pairs
        ]

    async def _private_menu_view(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        *,
        issued_at: int | None = None,
        page: int = 0,
    ) -> discord.ui.LayoutView:
        locale = self._locale(interaction)
        pairs = await self._menu_pairs_for_interaction(client, interaction)
        return self._menu_view_for_pairs(
            locale,
            pairs,
            requester_user_id=interaction.user.id,
            issued_at=issued_at,
            page=page,
        )

    def _menu_view_for_pairs(
        self: Manager,
        locale: object,
        pairs: list[tuple[ServerDiscordBinding, Server]],
        *,
        requester_user_id: int | str,
        issued_at: int | None = None,
        page: int = 0,
    ) -> discord.ui.LayoutView:
        if not pairs:
            return host.no_access_view(locale)
        issued_at = issued_at or host.menu_issued_at()
        if len(pairs) == 1:
            binding, server = pairs[0]
            return host.control_view(
                locale,
                server_id=server.id,
                server_name=server.name,
                capabilities=binding.capabilities or [],
                issued_at=issued_at,
                requester_user_id=requester_user_id,
            )
        return host.server_picker_view(
            locale,
            self._menu_server_payload(pairs),
            issued_at=issued_at,
            requester_user_id=requester_user_id,
            page=page,
        )

    async def command_menu(
        self: Manager, client: ManagedDiscordClient, interaction: discord.Interaction
    ) -> None:
        try:
            view = await self._private_menu_view(client, interaction)
            await interaction.response.send_message(view=view, ephemeral=True)
        except Exception as exc:
            await self._respond_error(interaction, exc)

    async def _menu_control_view(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        *,
        issued_at: int,
        server_id: int,
    ) -> discord.ui.LayoutView:
        pairs = await self._menu_pairs_for_interaction(client, interaction)
        for binding, server in pairs:
            if server.id == server_id:
                return host.control_view(
                    self._locale(interaction),
                    server_id=server.id,
                    server_name=server.name,
                    capabilities=binding.capabilities or [],
                    issued_at=issued_at,
                    requester_user_id=interaction.user.id,
                )
        raise host.DiscordAuthorizationDenied("Selected server is unavailable or unauthorized")

    async def _resolve_menu_server(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        server_id: int,
        capability: DiscordCapability,
    ) -> Server:
        pairs = await self._menu_pairs_for_interaction(client, interaction, capability)
        for _binding, server in pairs:
            if server.id == server_id:
                return server
        raise host.DiscordAuthorizationDenied("Selected server is unavailable or unauthorized")

    async def _menu_expired_response(self: Manager, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            host.menu_text(self._locale(interaction), "expired"), ephemeral=True
        )

    async def _resolve_server(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        capability: DiscordCapability,
        server_value: str | None,
    ) -> Server:
        if interaction.guild_id is None or interaction.channel_id is None:
            raise host.DiscordAuthorizationDenied(
                "Commands are only available in configured Guild channels"
            )
        async with host.async_session_maker() as db:
            pairs = await host.authorized_bindings(
                db,
                bot_owner_user_id=client.owner_user_id,
                guild_id=str(interaction.guild_id),
                channel_id=str(interaction.channel_id),
                actor_user_id=str(interaction.user.id),
                actor_role_ids=host._roles(interaction),
                actor_is_channel_manager=host._is_channel_manager(interaction),
                actor_is_server_administrator=host._is_server_administrator(interaction),
                required_capability=capability,
            )
            if server_value:
                with suppress(ValueError):
                    server_id = int(server_value)
                    for _binding, server in pairs:
                        if server.id == server_id:
                            return server
                raise host.DiscordAuthorizationDenied(
                    "Selected server is unavailable or unauthorized"
                )
            if len(pairs) == 1:
                return pairs[0][1]
            if not pairs:
                raise host.DiscordAuthorizationDenied(
                    "No authorized server is available for this command"
                )
            raise host.DiscordAuthorizationDenied(
                "Multiple servers are available; select one explicitly"
            )

    async def _respond_error(
        self: Manager, interaction: discord.Interaction, exc: Exception
    ) -> None:
        message = host._public_error_text(exc)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException as send_exc:
            host.logger.warning("Unable to send Discord error reply: %s", send_exc)
