"""Discord bot manager mixin: DiscordMenuMixin."""

from __future__ import annotations

import discord

from modules.models import (
    ManagedPlugin,
    Server,
)
from modules.schemas.discord import DiscordCapability
from services.change_map_service import MapCandidate
from services.compat import LateBoundModule
from services.discord.client import ManagedDiscordClient
from services.discord.types import Manager

host = LateBoundModule("services.discord_bot_manager")


class DiscordMenuMixin:
    async def _managed_plugin_view(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        *,
        issued_at: int,
        server_id: int,
        page: int,
        mode: str,
    ) -> discord.ui.LayoutView:
        capability = (
            DiscordCapability.PLUGIN_UPGRADE
            if mode == "upgrade"
            else DiscordCapability.PLUGIN_BROWSE
        )
        server = await self._resolve_menu_server(client, interaction, server_id, capability)
        async with host.async_session_maker() as db:
            total = int(
                await db.scalar(
                    host.select(host.func.count())
                    .select_from(ManagedPlugin)
                    .where(ManagedPlugin.server_id == server.id)
                )
                or 0
            )
            pages = max(1, host.math.ceil(total / host.PLUGIN_PAGE_SIZE))
            page = min(max(page, 0), pages - 1)
            result = await db.execute(
                host.select(ManagedPlugin)
                .where(ManagedPlugin.server_id == server.id)
                .order_by(
                    host.col(ManagedPlugin.display_name).asc(), host.col(ManagedPlugin.id).asc()
                )
                .offset(page * host.PLUGIN_PAGE_SIZE)
                .limit(host.PLUGIN_PAGE_SIZE)
            )
            plugins = list(result.scalars().all())
        options = [
            discord.SelectOption(
                label=item.display_name[:100],
                value=str(item.id),
                description=f"{item.installed_version} → {item.latest_version or 'unknown'}"[:100],
                emoji="🧩",
            )
            for item in plugins
            if item.id is not None
        ]
        locale = self._locale(interaction)
        return host.plugin_picker_view(
            locale,
            title=host.menu_text(locale, "managed_plugins", server=server.name),
            hint=host.menu_text(locale, "managed_hint"),
            options=options,
            custom_id=(
                f"cs2:menu:managed_pick:{issued_at}:{interaction.user.id}:{server.id}:{mode}"
            ),
            issued_at=issued_at,
            requester_user_id=interaction.user.id,
            server_id=server.id,
            page=page,
            pages=pages,
            page_kind=f"managed_{mode}",
        )

    @staticmethod
    def _search_state_key(
        client: ManagedDiscordClient, interaction: discord.Interaction, nonce: str
    ) -> str:
        return f"discord_menu_search:{client.owner_user_id}:{interaction.user.id}:{nonce}"

    async def _market_search_view(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        *,
        issued_at: int,
        server_id: int,
        nonce: str,
        page: int,
    ) -> discord.ui.LayoutView:
        state = await host.redis_manager.get(self._search_state_key(client, interaction, nonce))
        if not isinstance(state, dict):
            raise host.DiscordAuthorizationDenied("Plugin search expired; open a new menu")
        query = str(state.get("query") or "").strip()
        mode = str(state.get("mode") or "browse")
        if mode not in {"browse", "install"} or state.get("server_id") != server_id:
            raise host.DiscordAuthorizationDenied("Plugin search state does not match this menu")
        capability = (
            DiscordCapability.PLUGIN_INSTALL
            if mode == "install"
            else DiscordCapability.PLUGIN_BROWSE
        )
        await self._resolve_menu_server(client, interaction, server_id, capability)
        async with host.async_session_maker() as db:
            plugins, total = await host.MarketPlugin.search_plugins(
                db,
                search_query=query,
                skip=max(0, page) * host.PLUGIN_PAGE_SIZE,
                limit=host.PLUGIN_PAGE_SIZE,
            )
        pages = max(1, host.math.ceil(total / host.PLUGIN_PAGE_SIZE))
        page = min(max(page, 0), pages - 1)
        if page and not plugins:
            async with host.async_session_maker() as db:
                plugins, _total = await host.MarketPlugin.search_plugins(
                    db,
                    search_query=query,
                    skip=page * host.PLUGIN_PAGE_SIZE,
                    limit=host.PLUGIN_PAGE_SIZE,
                )
        options = [
            discord.SelectOption(
                label=item.title[:100],
                value=str(item.id),
                description=f"{item.version or 'unknown'} · {item.author or 'unknown'}"[:100],
                emoji="📦",
            )
            for item in plugins
            if item.id is not None
        ]
        locale = self._locale(interaction)
        return host.plugin_picker_view(
            locale,
            title=host.menu_text(locale, "search_results", query=host._safe_text(query, 100)),
            hint=host.menu_text(locale, "search_result_hint"),
            options=options,
            custom_id=(
                f"cs2:menu:market_pick:{issued_at}:{interaction.user.id}:{server_id}:{nonce}:{mode}"
            ),
            issued_at=issued_at,
            requester_user_id=interaction.user.id,
            server_id=server_id,
            page=page,
            pages=pages,
            page_kind=f"search_{nonce}",
        )

    async def _menu_search_submit(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        *,
        issued_at: int,
        server_id: int,
        mode: str,
        query: str,
    ) -> None:
        if mode not in {"browse", "install"}:
            raise host.DiscordAuthorizationDenied("Invalid plugin search mode")
        capability = (
            DiscordCapability.PLUGIN_INSTALL
            if mode == "install"
            else DiscordCapability.PLUGIN_BROWSE
        )
        await self._resolve_menu_server(client, interaction, server_id, capability)
        await interaction.response.defer(ephemeral=True, thinking=True)
        nonce = host.uuid.uuid4().hex[:12]
        saved = await host.redis_manager.set(
            self._search_state_key(client, interaction, nonce),
            {"query": query.strip(), "mode": mode, "server_id": server_id},
            host.MENU_LIFETIME_SECONDS,
        )
        if not saved:
            raise host.DiscordAuthorizationDenied("Plugin search state is temporarily unavailable")
        view = await self._market_search_view(
            client,
            interaction,
            issued_at=issued_at,
            server_id=server_id,
            nonce=nonce,
            page=0,
        )
        await host._edit_interaction_message(interaction, content=None, view=view)

    async def _menu_console_submit(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        *,
        server_id: int,
        command: str,
    ) -> None:
        data = host.GameConsoleCommandInput(command=command)
        server = await self._resolve_menu_server(
            client, interaction, server_id, DiscordCapability.GAME_CONSOLE
        )
        await interaction.response.defer(ephemeral=True, thinking=True)
        command_hash = host.hashlib.sha256(data.command.encode()).hexdigest()
        await self._publish_menu_confirmation(
            interaction,
            server,
            "game_console",
            DiscordCapability.GAME_CONSOLE,
            {
                "command_encrypted": host.encrypt_credential(data.command),
                "command_hash": command_hash,
            },
            host._game_console_plan(server, data.command),
        )

    async def _change_map_arguments(self: Manager, candidate: MapCandidate) -> dict:
        command = candidate.command
        return {
            "name": candidate.name,
            "workshop_id": candidate.workshop_id,
            "filename": candidate.filename,
            "command_encrypted": host.encrypt_credential(command),
            "command_hash": host.hashlib.sha256(command.encode()).hexdigest(),
        }

    async def _confirm_change_map(
        self: Manager,
        interaction: discord.Interaction,
        server: Server,
        candidate: MapCandidate,
        *,
        publish: str,
    ) -> None:
        arguments = await self._change_map_arguments(candidate)
        plan = host._change_map_plan(server, candidate)
        if publish == "menu":
            await self._publish_menu_confirmation(
                interaction,
                server,
                "change_map",
                DiscordCapability.CHANGE_MAP,
                arguments,
                plan,
            )
            return
        await self._send_confirmation(
            interaction,
            server,
            "change_map",
            DiscordCapability.CHANGE_MAP,
            arguments,
            plan,
        )

    async def _start_change_map(
        self: Manager,
        interaction: discord.Interaction,
        server: Server,
        query: str,
        *,
        publish: str,
        issued_at: int | None = None,
    ) -> None:
        unique, matches = host.resolve_change_map(await host.load_map_pool(server), query)
        if unique is not None:
            await self._confirm_change_map(interaction, server, unique, publish=publish)
            return
        if not matches:
            raise host.ChangeMapError(f"No map matched {query!r}")
        if publish == "slash":
            raise host.ChangeMapAmbiguousError(matches)
        if issued_at is None:
            raise host.DiscordAuthorizationDenied("Map picker is unavailable")
        nonce = host.uuid.uuid4().hex[:12]
        saved = await host.redis_manager.set(
            self._map_state_key(interaction, nonce),
            {
                "query": query.strip(),
                "server_id": server.id,
                "matches": [
                    {
                        "name": item.name,
                        "workshop_id": item.workshop_id,
                        "enabled": item.enabled,
                        "filename": item.filename,
                        "updated_name": item.updated_name,
                    }
                    for item in matches
                ],
            },
            host.MENU_LIFETIME_SECONDS,
        )
        if not saved:
            raise host.DiscordAuthorizationDenied("Map search state is temporarily unavailable")
        view = await self._map_picker_view(
            interaction,
            issued_at=issued_at,
            server_id=server.id,
            nonce=nonce,
            page=0,
        )
        await host._edit_interaction_message(interaction, content=None, view=view)

    @staticmethod
    def _map_state_key(interaction: discord.Interaction, nonce: str) -> str:
        return f"discord_menu_map:{interaction.user.id}:{nonce}"

    async def _map_picker_view(
        self: Manager,
        interaction: discord.Interaction,
        *,
        issued_at: int,
        server_id: int,
        nonce: str,
        page: int,
    ) -> discord.ui.LayoutView:
        state = await host.redis_manager.get(self._map_state_key(interaction, nonce))
        if not isinstance(state, dict) or state.get("server_id") != server_id:
            raise host.DiscordAuthorizationDenied("Map search expired; open a new menu")
        matches = [
            host.candidate_from_map(item)
            for item in state.get("matches") or []
            if isinstance(item, dict)
        ]
        pages = max(1, host.math.ceil(len(matches) / host.PLUGIN_PAGE_SIZE))
        page = min(max(page, 0), pages - 1)
        visible = matches[page * host.PLUGIN_PAGE_SIZE : (page + 1) * host.PLUGIN_PAGE_SIZE]
        locale = self._locale(interaction)
        options = [
            discord.SelectOption(
                label=item.name[:100],
                value=item.identity_key[:100],
                description=item.display_label()[:100],
                emoji="🗺️",
            )
            for item in visible
        ]
        return host.plugin_picker_view(
            locale,
            title=host.menu_text(
                locale, "change_map_results", query=host._safe_text(state.get("query") or "", 100)
            ),
            hint=host.menu_text(locale, "change_map_hint"),
            options=options,
            custom_id=(f"cs2:menu:maps_pick:{issued_at}:{interaction.user.id}:{server_id}:{nonce}"),
            issued_at=issued_at,
            requester_user_id=interaction.user.id,
            server_id=server_id,
            page=page,
            pages=pages,
            page_kind=f"maps_{nonce}",
        )

    async def _menu_map_submit(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        *,
        issued_at: int,
        server_id: int,
        query: str,
    ) -> None:
        server = await self._resolve_menu_server(
            client, interaction, server_id, DiscordCapability.CHANGE_MAP
        )
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._start_change_map(
            interaction, server, query, publish="menu", issued_at=issued_at
        )

    async def _menu_agent_submit(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        *,
        server_id: int,
        prompt: str,
    ) -> None:
        server = await self._resolve_menu_server(
            client, interaction, server_id, DiscordCapability.AGENT_ASK
        )
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await interaction.followup.send(
            embed=discord.Embed(title="AI Agent", description="Working…"),
            ephemeral=False,
            wait=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if message is None:
            raise host.DiscordAuthorizationDenied("Discord did not return the AI progress message")
        try:
            run_id = await host.ask_discord_agent(
                owner_user_id=client.owner_user_id,
                server_id=server.id,
                actor_user_id=str(interaction.user.id),
                guild_id=str(interaction.guild_id),
                channel_id=str(interaction.channel_id),
                prompt=prompt,
            )
        except Exception:
            await host._edit_webhook_message(
                message,
                embed=discord.Embed(
                    title="AI Agent",
                    description="The request failed. Reopen the menu and try again.",
                    color=discord.Color.red(),
                ),
                view=None,
            )
            raise
        if not await self._render_ai_run_message(message, run_id):
            await self._render_ai_run(interaction, run_id)
        await host._edit_interaction_message(
            interaction, content=host.menu_text(self._locale(interaction), "published")
        )

    async def _handle_menu_action(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        *,
        issued_at: int,
        server_id: int,
        action: str,
    ) -> None:
        capability = host.action_capability(action)
        if capability is None:
            raise host.DiscordAuthorizationDenied("Unknown or unavailable menu action")
        server = await self._resolve_menu_server(client, interaction, server_id, capability)
        locale = self._locale(interaction)
        if action in {"plugin_search", "plugin_install"}:
            mode = "install" if action == "plugin_install" else "browse"
            modal = host.MenuInputModal(
                locale=locale,
                title_key="search_title",
                label_key="search_label",
                placeholder_key="search_placeholder",
                custom_id=(
                    f"cs2:menu:search_modal:{issued_at}:{interaction.user.id}:{server.id}:{mode}"
                ),
                callback=lambda modal_interaction, value: self._menu_search_submit(
                    client,
                    modal_interaction,
                    issued_at=issued_at,
                    server_id=server.id,
                    mode=mode,
                    query=value,
                ),
                max_length=200,
            )
            await interaction.response.send_modal(modal)
            return
        if action == "game_console":
            modal = host.MenuInputModal(
                locale=locale,
                title_key="console_title",
                label_key="console_label",
                placeholder_key="console_placeholder",
                custom_id=(f"cs2:menu:console_modal:{issued_at}:{interaction.user.id}:{server.id}"),
                callback=lambda modal_interaction, value: self._menu_console_submit(
                    client, modal_interaction, server_id=server.id, command=value
                ),
                max_length=500,
            )
            await interaction.response.send_modal(modal)
            return
        if action == "change_map":
            modal = host.MenuInputModal(
                locale=locale,
                title_key="change_map_title",
                label_key="change_map_label",
                placeholder_key="change_map_placeholder",
                custom_id=(f"cs2:menu:map_modal:{issued_at}:{interaction.user.id}:{server.id}"),
                callback=lambda modal_interaction, value: self._menu_map_submit(
                    client,
                    modal_interaction,
                    issued_at=issued_at,
                    server_id=server.id,
                    query=value,
                ),
                max_length=128,
            )
            await interaction.response.send_modal(modal)
            return
        if action == "agent_ask":
            modal = host.MenuInputModal(
                locale=locale,
                title_key="agent_title",
                label_key="agent_label",
                placeholder_key="agent_placeholder",
                custom_id=(f"cs2:menu:agent_modal:{issued_at}:{interaction.user.id}:{server.id}"),
                callback=lambda modal_interaction, value: self._menu_agent_submit(
                    client, modal_interaction, server_id=server.id, prompt=value
                ),
                style=discord.TextStyle.paragraph,
                max_length=1000,
            )
            await interaction.response.send_modal(modal)
            return
        if action in {"plugin_list", "plugin_upgrade"}:
            mode = "upgrade" if action == "plugin_upgrade" else "browse"
            view = await self._managed_plugin_view(
                client,
                interaction,
                issued_at=issued_at,
                server_id=server.id,
                page=0,
                mode=mode,
            )
            await interaction.response.edit_message(view=view)
            return
        if action == "status":
            await self._publish_menu_status(interaction, server)
            return
        if action in {"start", "stop", "restart", "update", "validate"}:
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self._publish_menu_confirmation(
                interaction,
                server,
                action,
                capability,
                {"action": action},
                host._simple_plan(server, action),
            )
            return
        if action == "agent_reset":
            await interaction.response.defer(ephemeral=True, thinking=True)
            await host.reset_discord_conversation(
                owner_user_id=client.owner_user_id,
                server_id=server.id,
                actor_user_id=str(interaction.user.id),
                guild_id=str(interaction.guild_id),
                channel_id=str(interaction.channel_id),
            )
            await interaction.followup.send(
                f"Started a new isolated AI context for **{server.name}**.",
                ephemeral=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await host._edit_interaction_message(
                interaction, content=host.menu_text(locale, "published")
            )
            return
        raise host.DiscordAuthorizationDenied("Unknown or unavailable menu action")
