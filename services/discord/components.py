"""Discord bot manager mixin: DiscordComponentsMixin."""

from __future__ import annotations

import asyncio

import discord

from modules.models import (
    DiscordOperationRun,
    Server,
    User,
)
from services.compat import LateBoundModule
from services.discord.client import ManagedDiscordClient
from services.discord.types import Manager

from .menu_component_handlers import (
    _menu_action,
    _menu_control,
    _menu_managed_page,
    _menu_managed_pick,
    _menu_maps_page,
    _menu_maps_pick,
    _menu_market_pick,
    _menu_open,
    _menu_page,
    _menu_search_page,
    _menu_server,
)

host = LateBoundModule("services.discord_bot_manager")


async def _menu_identity(
    self: Manager, interaction: discord.Interaction, parts: list[str]
) -> int | None:
    if len(parts) < 5:
        raise host.DiscordAuthorizationDenied("Invalid menu component")
    try:
        issued_at = int(parts[3])
        requester_user_id = int(parts[4])
    except ValueError as exc:
        raise host.DiscordAuthorizationDenied("Invalid menu identity") from exc
    if requester_user_id != interaction.user.id:
        raise host.DiscordAuthorizationDenied(
            host.menu_text(self._locale(interaction), "not_owner")
        )
    if host.menu_is_expired(issued_at):
        await self._menu_expired_response(interaction)
        return None
    return issued_at


class DiscordComponentsMixin:
    async def _handle_menu_component(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        parts: list[str],
    ) -> None:
        kind = parts[2] if len(parts) > 2 else ""
        if kind == "open":
            await _menu_open(self, client, interaction, parts)
            return
        issued_at = await _menu_identity(self, interaction, parts)
        if issued_at is None:
            return
        if kind.startswith("managed_") and kind != "managed_pick":
            await _menu_managed_page(self, client, interaction, parts, issued_at)
            return
        if kind.startswith("maps_") and kind != "maps_pick":
            await _menu_maps_page(self, client, interaction, parts, issued_at)
            return
        if kind.startswith("search_"):
            await _menu_search_page(self, client, interaction, parts, issued_at)
            return
        handlers = {
            "page": _menu_page,
            "server": _menu_server,
            "control": _menu_control,
            "action": _menu_action,
            "managed_pick": _menu_managed_pick,
            "maps_pick": _menu_maps_pick,
            "market_pick": _menu_market_pick,
        }
        handler = handlers.get(kind)
        if handler is None:
            raise host.DiscordAuthorizationDenied("Unknown menu component")
        await handler(self, client, interaction, parts, issued_at)

    async def handle_component(
        self: Manager,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        custom_id: str,
    ) -> None:
        try:
            parts = custom_id.split(":")
            if parts[1] == "menu":
                await self._handle_menu_component(client, interaction, parts)
            elif parts[1] == "op":
                operation_id, decision = parts[2], parts[3]
                if decision == "cancel":
                    await self._cancel_operation(interaction, operation_id)
                    return
                await interaction.response.defer(ephemeral=False)
                await self._confirm_and_execute(interaction, operation_id)
            elif parts[1] == "ai":
                run_id, tool_run_id = parts[2], parts[3]
                await interaction.response.defer(ephemeral=False)
                task = host.asyncio.create_task(
                    host.approve_discord_tool(
                        run_id=run_id,
                        tool_run_id=tool_run_id,
                        actor_user_id=str(interaction.user.id),
                        actor_role_ids=host._roles(interaction),
                        actor_is_channel_manager=host._is_channel_manager(interaction),
                        actor_is_server_administrator=host._is_server_administrator(interaction),
                        guild_id=str(interaction.guild_id),
                        channel_id=str(interaction.channel_id),
                    )
                )
                while not task.done():
                    snapshot = await host.discord_run_snapshot(run_id)
                    progress = snapshot.get("progress") or {}
                    detail = progress.get("snapshot") or {}
                    message = (
                        detail.get("message")
                        or f"{progress.get('tool', 'AI tool')}: {progress.get('status', 'running')}"
                    )
                    await host._edit_interaction_message(
                        interaction,
                        embed=discord.Embed(
                            title="AI Agent operation",
                            description=host._safe_text(message, 3500),
                            color=discord.Color.blurple(),
                        ),
                        view=None,
                    )
                    try:
                        await asyncio.wait_for(asyncio.shield(task), timeout=2)
                    except TimeoutError:
                        continue
                await task
                await self._render_ai_run(interaction, run_id)
        except Exception as exc:
            await self._respond_error(interaction, exc)

    async def _cancel_operation(
        self: Manager, interaction: discord.Interaction, operation_id: str
    ) -> None:
        async with host.async_session_maker() as db:
            item = await db.get(DiscordOperationRun, operation_id)
            if item is None or item.status != "pending":
                raise host.DiscordOperationDenied("Operation is no longer pending")
            if item.actor_user_id != str(interaction.user.id):
                raise host.DiscordOperationDenied("Only the original requester may cancel")
            item.status = "cancelled"
            item.completed_at = host.get_current_time()
            db.add(item)
            await db.commit()
        await host.record_discord_operation_event(item, "cancelled")
        await host._publish_interaction_update(
            interaction,
            embed=discord.Embed(title="Operation cancelled", color=discord.Color.greyple()),
            view=None,
        )

    async def _fresh_plan(self: Manager, item: DiscordOperationRun, server: Server) -> dict:
        if item.action == "plugin_install":
            plan, _arguments, _warnings = await self._market_plan(
                server, int(item.arguments["plugin_id"])
            )
            return plan
        if item.action == "plugin_upgrade":
            from services.plugin_auto_update_service import plugin_auto_update_service

            return await plugin_auto_update_service.build_plugin_upgrade_plan(
                server.id, int(item.arguments["plugin_id"])
            )
        if item.action == "game_console":
            return host._game_console_plan(server, host._operation_game_console_command(item))
        if item.action == "change_map":
            return host._change_map_plan(server, host._operation_change_map_candidate(item))
        return host._simple_plan(server, item.action)

    async def _confirm_and_execute(
        self: Manager, interaction: discord.Interaction, operation_id: str
    ) -> None:
        async with host.async_session_maker() as db:
            pending = await db.get(DiscordOperationRun, operation_id)
            if pending is None:
                raise host.DiscordOperationDenied("Operation not found")
            server = await db.get(Server, pending.server_id)
            if server is None:
                raise host.DiscordOperationDenied("Server not found")
        fresh_plan = await self._fresh_plan(pending, server)
        async with host.async_session_maker() as db:
            item = await host.confirm_operation(
                db,
                operation_id=operation_id,
                actor_user_id=str(interaction.user.id),
                actor_role_ids=host._roles(interaction),
                actor_is_channel_manager=host._is_channel_manager(interaction),
                actor_is_server_administrator=host._is_server_administrator(interaction),
                fresh_plan=fresh_plan,
            )
            item.status = "running"
            db.add(item)
            await db.commit()
        await host._publish_interaction_update(
            interaction,
            embed=discord.Embed(title=f"Running {item.action}", description="Starting…"),
            view=None,
        )
        try:
            result = await self._execute_operation(interaction, item)
        except Exception as exc:
            safe_error = host._safe_text(str(exc), 1800)
            async with host.async_session_maker() as db:
                saved = await db.get(DiscordOperationRun, item.id)
                if saved:
                    saved.status = "failed"
                    saved.error = safe_error
                    saved.result = {"success": False, "error": safe_error}
                    saved.completed_at = host.get_current_time()
                    db.add(saved)
                    await db.commit()
                    await host.record_discord_operation_event(saved, "failure")
            await host._publish_interaction_update(
                interaction,
                embed=discord.Embed(
                    title=f"{item.action} failed",
                    description=safe_error,
                    color=discord.Color.red(),
                ),
                view=None,
            )
            return
        async with host.async_session_maker() as db:
            saved = await db.get(DiscordOperationRun, item.id)
            if saved:
                saved.status = "completed" if result.get("success", True) else "failed"
                saved.result = result
                saved.completed_at = host.get_current_time()
                db.add(saved)
                await db.commit()
                await host.record_discord_operation_event(
                    saved, "success" if saved.status == "completed" else "failure"
                )
        await host._publish_interaction_update(
            interaction,
            embed=discord.Embed(
                title=f"{item.action} completed",
                description=host._safe_text(result),
                color=discord.Color.green() if result.get("success", True) else discord.Color.red(),
            ),
            view=None,
        )

    async def _execute_operation(
        self: Manager, interaction: discord.Interaction, item: DiscordOperationRun
    ) -> dict:
        async with host.async_session_maker() as db:
            server = await db.get(Server, item.server_id)
            owner = await db.get(User, item.owner_user_id)
            if server is None or owner is None or not owner.is_active or server.user_id != owner.id:
                raise host.DiscordOperationDenied("Server ownership is no longer valid")

            async def progress(_event: str, payload: dict) -> None:
                message = payload.get("message") or "Working…"
                await host._edit_interaction_message(
                    interaction,
                    embed=discord.Embed(
                        title=f"Running {item.action}", description=host._safe_text(message, 3500)
                    ),
                )

            context = host.ToolContext(
                db=db,
                user=owner,
                server=server,
                emit=progress,
                run_id=f"discord:{item.id}",
                enforce_agent_policy=False,
            )
            if item.action in {"start", "stop", "restart"}:
                spec = host.TOOLS_BY_NAME["control_server"]
                data = host.ServerControlInput.model_validate({"action": item.action})
                return await spec.handler(context, data)
            if item.action in {"update", "validate"}:
                spec = host.TOOLS_BY_NAME["run_server_operation"]
                data = host.ServerOperationInput.model_validate({"operation": item.action})
                return await spec.handler(context, data)
            if item.action == "plugin_install":
                spec = host.TOOLS_BY_NAME["apply_plugin_plan"]
                data = host.ApplyPluginPlanInput.model_validate(item.arguments)
                return await spec.handler(context, data)
            if item.action in {"game_console", "change_map"}:
                spec = host.TOOLS_BY_NAME["send_game_console_command"]
                data = host.GameConsoleCommandInput(
                    command=host._operation_game_console_command(item)
                )
                return await spec.handler(context, data)
        if item.action == "plugin_upgrade":
            from services.operation_enqueue import enqueue_plugin_auto_update
            from services.server_operation_hub import server_operation_hub

            record = await enqueue_plugin_auto_update(
                server_id=item.server_id,
                actor_user_id=item.owner_user_id,
                plugin_id=int(item.arguments["plugin_id"]),
                force=True,
            )
            final = await server_operation_hub.wait_until_terminal(str(record["operation_id"]))
            return {
                "success": bool(final.get("success")),
                "message": str(final.get("message") or ""),
            }
        raise ValueError(f"Unsupported Discord operation: {item.action}")
