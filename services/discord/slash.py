"""Discord bot manager mixin: DiscordSlashMixin."""

from __future__ import annotations

import discord

from modules.models import (
    DiscordOperationRun,
    ManagedPlugin,
    MarketPlugin,
    Server,
)
from modules.schemas.discord import DiscordCapability
from services.compat import LateBoundModule
from services.discord.client import ManagedDiscordClient, _AIConfirmView, _ConfirmView
from services.discord.types import Manager

host = LateBoundModule("services.discord_bot_manager")


class DiscordSlashMixin:
    async def command_help(
        self: Manager, client: ManagedDiscordClient, interaction: discord.Interaction
    ) -> None:
        try:
            if interaction.guild_id is None or interaction.channel_id is None:
                raise host.DiscordAuthorizationDenied(
                    "Use this command in a configured Guild channel"
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
                )
            if not pairs:
                raise host.DiscordAuthorizationDenied("You are not on the server whitelist")
        except Exception as exc:
            await self._respond_error(interaction, exc)
            return
        await interaction.response.send_message(
            "`/cs2 status|start|stop|restart|update|validate|map`\n"
            "`/cs2 menu`\n"
            "`/cs2 plugin search|list|install|upgrade`\n"
            "`/cs2 console send`\n"
            "`/cs2 agent ask|reset`\n"
            "Write operations always require a public confirmation by the requester.",
            ephemeral=False,
        )

    async def command_status(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        server_value: str | None,
    ) -> None:
        try:
            server = await self._resolve_server(
                client, interaction, DiscordCapability.STATUS, server_value
            )
            await interaction.response.defer(ephemeral=False)
            await host._publish_interaction_update(
                interaction,
                embed=await self._status_embed(server, self._locale(interaction)),
            )
        except Exception as exc:
            await self._respond_error(interaction, exc)

    async def _status_embed(
        self: Manager, server: Server, locale: object = "en-US"
    ) -> discord.Embed:
        sources = await host.load_panel_status_sources(server)
        payload = sources["info"] if sources["a2s_ok"] and isinstance(sources["info"], dict) else {}
        title_name = str(payload.get("server_name") or "").strip() or server.name
        embed = discord.Embed(
            title=title_name,
            description=host.menu_text(
                locale, "status_online" if sources["a2s_ok"] else "status_offline"
            ),
            color=discord.Color.green() if sources["a2s_ok"] else discord.Color.orange(),
        )
        for name, value in host.status_card_fields(server, locale=locale, **sources):
            embed.add_field(
                name=name,
                value=value or host.menu_text(locale, "status_unknown"),
                inline=True,
            )
        return embed

    async def _send_confirmation(
        self: Manager,
        interaction: discord.Interaction,
        server: Server,
        action: str,
        capability: DiscordCapability,
        arguments: dict,
        plan: dict,
        *,
        warnings: bool = False,
    ) -> None:
        operation, embed, view = await self._build_confirmation(
            interaction,
            server,
            action,
            capability,
            arguments,
            plan,
            warnings=warnings,
        )
        try:
            await interaction.edit_original_response(embed=embed, view=view)
            message = await interaction.original_response()
        except discord.HTTPException as exc:
            if not host._is_unknown_discord_resource(exc):
                raise
            message = await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=False,
                wait=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        if message is None:
            raise host.DiscordOperationDenied("Discord did not return the confirmation message")
        await self._save_operation_message(operation.id, message.id)

    async def _build_confirmation(
        self: Manager,
        interaction: discord.Interaction,
        server: Server,
        action: str,
        capability: DiscordCapability,
        arguments: dict,
        plan: dict,
        *,
        warnings: bool = False,
    ) -> tuple[DiscordOperationRun, discord.Embed, _ConfirmView]:
        async with host.async_session_maker() as db:
            operation = await host.create_operation(
                db,
                server=server,
                actor_user_id=str(interaction.user.id),
                actor_role_ids=host._roles(interaction),
                actor_is_channel_manager=host._is_channel_manager(interaction),
                actor_is_server_administrator=host._is_server_administrator(interaction),
                guild_id=str(interaction.guild_id),
                channel_id=str(interaction.channel_id),
                action=action,
                required_capabilities=[capability],
                arguments=arguments,
                plan=plan,
            )
        embed = discord.Embed(
            title=f"Confirm {action}",
            description=f"Server: **{server.name}**\nExpires in 15 minutes.",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Immutable plan", value=f"```json\n{host._safe_text(plan, 900)}\n```", inline=False
        )
        return operation, embed, _ConfirmView(operation.id, warnings=warnings)

    async def _save_operation_message(self: Manager, operation_id: str, message_id: int) -> None:
        async with host.async_session_maker() as db:
            saved = await db.get(DiscordOperationRun, operation_id)
            if saved:
                saved.message_id = str(message_id)
                db.add(saved)
                await db.commit()

    async def _publish_menu_confirmation(
        self: Manager,
        interaction: discord.Interaction,
        server: Server,
        action: str,
        capability: DiscordCapability,
        arguments: dict,
        plan: dict,
        *,
        warnings: bool = False,
    ) -> None:
        operation, embed, view = await self._build_confirmation(
            interaction,
            server,
            action,
            capability,
            arguments,
            plan,
            warnings=warnings,
        )
        message = await interaction.followup.send(
            embed=embed,
            view=view,
            ephemeral=False,
            wait=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if message is None:
            raise host.DiscordOperationDenied("Discord did not return the confirmation message")
        await self._save_operation_message(operation.id, message.id)
        await host._edit_interaction_message(
            interaction, content=host.menu_text(self._locale(interaction), "published")
        )

    async def command_write(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        action: str,
        server_value: str | None,
    ) -> None:
        try:
            capability = DiscordCapability(action)
            server = await self._resolve_server(client, interaction, capability, server_value)
            await interaction.response.defer(ephemeral=False)
            await self._send_confirmation(
                interaction,
                server,
                action,
                capability,
                {"action": action},
                host._simple_plan(server, action),
            )
        except Exception as exc:
            await self._respond_error(interaction, exc)

    async def command_game_console(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        command: str,
        server_value: str | None,
    ) -> None:
        try:
            data = host.GameConsoleCommandInput(command=command)
            server = await self._resolve_server(
                client, interaction, DiscordCapability.GAME_CONSOLE, server_value
            )
            await interaction.response.defer(ephemeral=False)
            command_hash = host.hashlib.sha256(data.command.encode()).hexdigest()
            await self._send_confirmation(
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
        except Exception as exc:
            await self._respond_error(interaction, exc)

    async def command_change_map(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        query: str,
        server_value: str | None,
    ) -> None:
        try:
            server = await self._resolve_server(
                client, interaction, DiscordCapability.CHANGE_MAP, server_value
            )
            await interaction.response.defer(ephemeral=False)
            await self._start_change_map(interaction, server, query, publish="slash")
        except Exception as exc:
            await self._respond_error(interaction, exc)

    async def command_plugin_search(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        query: str,
        server_value: str | None,
    ) -> None:
        try:
            await self._resolve_server(
                client, interaction, DiscordCapability.PLUGIN_BROWSE, server_value
            )
            async with host.async_session_maker() as db:
                plugins, total = await host.MarketPlugin.search_plugins(
                    db, search_query=query, limit=10
                )
            lines = [
                f"`{item.id}` **{item.title}** — {item.version or 'unknown'}" for item in plugins
            ]
            await interaction.response.send_message(
                f"Found {total} plugin(s)\n" + ("\n".join(lines) or "No matches"),
                ephemeral=False,
            )
        except Exception as exc:
            await self._respond_error(interaction, exc)

    async def command_plugin_list(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        server_value: str | None,
    ) -> None:
        try:
            server = await self._resolve_server(
                client, interaction, DiscordCapability.PLUGIN_BROWSE, server_value
            )
            async with host.async_session_maker() as db:
                result = await db.execute(
                    host.select(ManagedPlugin)
                    .where(ManagedPlugin.server_id == server.id)
                    .order_by(host.col(ManagedPlugin.display_name).asc())
                )
                plugins = list(result.scalars().all())
            lines = [
                f"`{item.id}` **{item.display_name}** — {item.installed_version}"
                for item in plugins[:25]
            ]
            await interaction.response.send_message(
                "\n".join(lines) if lines else "No managed plugins", ephemeral=False
            )
        except Exception as exc:
            await self._respond_error(interaction, exc)

    async def _market_plan(
        self: Manager, server: Server, plugin_id: int
    ) -> tuple[dict, dict, bool]:
        from services.plugin_conflict_service import build_plugin_install_plan

        async with host.async_session_maker() as db:
            plan = await build_plugin_install_plan(db, server.id, plugin_id, server=server)
        if plan.get("hard_conflicts"):
            raise ValueError(
                "Hard plugin conflict: " + host._safe_text(plan["hard_conflicts"], 1000)
            )
        warning_ids = [
            int(item["rule_id"])
            for item in plan.get("warnings", [])
            if isinstance(item, dict) and item.get("rule_id") is not None
        ]
        stable_plan = {
            "server_id": server.id,
            "plugin": plan.get("plugin"),
            "steps": plan.get("steps", []),
            "warnings": plan.get("warnings", []),
            "plan_hash": plan["plan_hash"],
            "dependencies_limited_to_plan": True,
        }
        arguments = {
            "plugin_id": plugin_id,
            "expected_plan_hash": plan["plan_hash"],
            "acknowledge_warning_rule_ids": warning_ids,
        }
        return stable_plan, arguments, bool(plan.get("warnings"))

    async def command_plugin_install(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        plugin_id: int,
        server_value: str | None,
    ) -> None:
        try:
            server = await self._resolve_server(
                client, interaction, DiscordCapability.PLUGIN_INSTALL, server_value
            )
            await interaction.response.defer(ephemeral=False)
            plan, arguments, warnings = await self._market_plan(server, plugin_id)
            await self._send_confirmation(
                interaction,
                server,
                "plugin_install",
                DiscordCapability.PLUGIN_INSTALL,
                arguments,
                plan,
                warnings=warnings,
            )
        except Exception as exc:
            await self._respond_error(interaction, exc)

    async def command_plugin_upgrade(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        plugin_id: int,
        server_value: str | None,
    ) -> None:
        try:
            server = await self._resolve_server(
                client, interaction, DiscordCapability.PLUGIN_UPGRADE, server_value
            )
            await interaction.response.defer(ephemeral=False)
            from services.plugin_auto_update_service import plugin_auto_update_service

            plan = await plugin_auto_update_service.build_plugin_upgrade_plan(server.id, plugin_id)
            if plan["no_op"]:
                await host._publish_interaction_update(
                    interaction,
                    content=f"{plan['name']} is already up to date.",
                    embed=None,
                    view=None,
                )
                return
            await self._send_confirmation(
                interaction,
                server,
                "plugin_upgrade",
                DiscordCapability.PLUGIN_UPGRADE,
                {"plugin_id": plugin_id},
                plan,
            )
        except Exception as exc:
            await self._respond_error(interaction, exc)

    async def command_agent_ask(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        prompt: str,
        server_value: str | None,
    ) -> None:
        try:
            server = await self._resolve_server(
                client, interaction, DiscordCapability.AGENT_ASK, server_value
            )
            await interaction.response.defer(ephemeral=False)
            await host._publish_interaction_update(
                interaction,
                embed=discord.Embed(title="AI Agent", description="Working…"),
            )
            run_id = await host.ask_discord_agent(
                owner_user_id=client.owner_user_id,
                server_id=server.id,
                actor_user_id=str(interaction.user.id),
                guild_id=str(interaction.guild_id),
                channel_id=str(interaction.channel_id),
                prompt=prompt,
            )
            await self._render_ai_run(interaction, run_id)
        except Exception as exc:
            await self._respond_error(interaction, exc)

    async def command_agent_reset(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        server_value: str | None,
    ) -> None:
        try:
            server = await self._resolve_server(
                client, interaction, DiscordCapability.AGENT_ASK, server_value
            )
            await host.reset_discord_conversation(
                owner_user_id=client.owner_user_id,
                server_id=server.id,
                actor_user_id=str(interaction.user.id),
                guild_id=str(interaction.guild_id),
                channel_id=str(interaction.channel_id),
            )
            await interaction.response.send_message(
                f"Started a new isolated AI context for **{server.name}**.", ephemeral=False
            )
        except Exception as exc:
            await self._respond_error(interaction, exc)

    async def _render_ai_run(self: Manager, interaction: discord.Interaction, run_id: str) -> None:
        snapshot = await host.discord_run_snapshot(run_id)
        if snapshot["status"] == "waiting_approval" and snapshot["tool"]:
            tool = snapshot["tool"]
            embed = discord.Embed(
                title=f"AI confirmation: {tool['name']}",
                description="Only the original requester can confirm. Expires in 15 minutes.",
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="Plan",
                value=f"```json\n{host._safe_text(tool['plan'], 900)}\n```",
                inline=False,
            )
            await host._publish_interaction_update(
                interaction, embed=embed, view=_AIConfirmView(run_id, tool["id"])
            )
            return
        color = discord.Color.green() if snapshot["status"] == "completed" else discord.Color.red()
        description = snapshot["message"] or snapshot["error"] or snapshot["status"]
        await host._publish_interaction_update(
            interaction,
            embed=discord.Embed(
                title="AI Agent", description=host._safe_text(description), color=color
            ),
            view=None,
        )

    async def _render_ai_run_message(
        self: Manager, message: discord.Message | discord.WebhookMessage, run_id: str
    ) -> bool:
        snapshot = await host.discord_run_snapshot(run_id)
        if snapshot["status"] == "waiting_approval" and snapshot["tool"]:
            tool = snapshot["tool"]
            embed = discord.Embed(
                title=f"AI confirmation: {tool['name']}",
                description="Only the original requester can confirm. Expires in 15 minutes.",
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="Plan",
                value=f"```json\n{host._safe_text(tool['plan'], 900)}\n```",
                inline=False,
            )
            return await host._edit_webhook_message(
                message, embed=embed, view=_AIConfirmView(run_id, tool["id"])
            )
        color = discord.Color.green() if snapshot["status"] == "completed" else discord.Color.red()
        description = snapshot["message"] or snapshot["error"] or snapshot["status"]
        return await host._edit_webhook_message(
            message,
            embed=discord.Embed(
                title="AI Agent", description=host._safe_text(description), color=color
            ),
            view=None,
        )

    async def _publish_menu_status(
        self: Manager, interaction: discord.Interaction, server: Server
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(
            embed=await self._status_embed(server, self._locale(interaction)),
            ephemeral=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await host._edit_interaction_message(
            interaction, content=host.menu_text(self._locale(interaction), "published")
        )

    @staticmethod
    def _plugin_embed(plugin: MarketPlugin | ManagedPlugin) -> discord.Embed:
        if isinstance(plugin, MarketPlugin):
            embed = discord.Embed(
                title=plugin.title,
                description=host._safe_text(plugin.description or "No description", 3000),
                color=discord.Color.blurple(),
            )
            embed.add_field(name="Market ID", value=str(plugin.id), inline=True)
            embed.add_field(name="Version", value=plugin.version or "unknown", inline=True)
            embed.add_field(name="Author", value=plugin.author or "unknown", inline=True)
            return embed
        embed = discord.Embed(
            title=plugin.display_name,
            description="Managed plugin",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Plugin ID", value=str(plugin.id), inline=True)
        embed.add_field(name="Installed", value=plugin.installed_version, inline=True)
        embed.add_field(name="Latest", value=plugin.latest_version or "unknown", inline=True)
        return embed
