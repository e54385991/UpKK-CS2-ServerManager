"""Lifecycle-managed per-user Discord Gateway clients and Slash Commands."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import uuid
from contextlib import suppress
from dataclasses import dataclass

import discord
from discord import app_commands
from sqlalchemy import func
from sqlmodel import select

from modules.database import async_session_maker
from modules.models import (
    DiscordOperationRun,
    ManagedPlugin,
    MarketPlugin,
    Server,
    ServerDiscordBinding,
    User,
    UserDiscordBot,
)
from modules.schemas.discord import DiscordCapability
from modules.utils import get_current_time
from services.ai_security import decrypt_credential, encrypt_credential, redact_sensitive_text
from services.ai_tools import (
    TOOLS_BY_NAME,
    ApplyPluginPlanInput,
    GameConsoleCommandInput,
    ServerControlInput,
    ServerOperationInput,
    ToolContext,
)
from services.discord_ai_service import (
    approve_discord_tool,
    ask_discord_agent,
    discord_run_snapshot,
    reset_discord_conversation,
)
from services.discord_authorization_service import (
    DiscordAuthorizationDenied,
    authorized_bindings,
)
from services.discord_bot_service import DISCORD_COMMAND_CHANNEL_TYPES
from services.discord_menu_ui import (
    MENU_LIFETIME_SECONDS,
    PLUGIN_PAGE_SIZE,
    MenuInputModal,
    action_capability,
    control_view,
    is_exact_wake_word,
    launcher_is_expired,
    launcher_view,
    menu_is_expired,
    menu_issued_at,
    no_access_view,
    normalize_message_trigger,
    plugin_picker_view,
    server_picker_view,
)
from services.discord_menu_ui import (
    text as menu_text,
)
from services.discord_operation_service import (
    DiscordOperationDenied,
    confirm_operation,
    create_operation,
)
from services.redis_manager import redis_manager

logger = logging.getLogger(__name__)
LEASE_TTL_SECONDS = 60
LEASE_RENEW_SECONDS = 20
RECONCILE_SECONDS = 30
MESSAGE_TRIGGER_MENTION_ONLY = "mention_only"
MESSAGE_TRIGGER_GREETINGS = "mention_and_greetings"


def _safe_text(value, limit: int = 3900) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return redact_sensitive_text(text, limit=limit)


def _roles(interaction: discord.Interaction) -> set[str]:
    member = interaction.user
    return {
        str(role.id)
        for role in getattr(member, "roles", [])
        if getattr(role, "id", None) is not None
    }


def _member_roles(member: object) -> set[str]:
    return {
        str(role.id)
        for role in getattr(member, "roles", [])
        if getattr(role, "id", None) is not None
    }


def _simple_plan(server: Server, action: str) -> dict:
    return {
        "server_id": server.id,
        "server_name": server.name,
        "action": action,
        "steps": ["acquire maintenance lock", f"execute {action}", "report final result"],
    }


def _game_console_plan(server: Server, command: str) -> dict:
    """Build a deterministic public plan without persisting the raw command in JSON."""

    return {
        "server_id": server.id,
        "server_name": server.name,
        "action": "game_console",
        "target": "running CS2 game-process console (not host Shell)",
        "command": redact_sensitive_text(command, limit=500),
        "command_hash": hashlib.sha256(command.encode()).hexdigest(),
        "steps": [
            "acquire maintenance lock",
            "locate the exact screen/tmux game session",
            "send literal command input followed by Enter",
        ],
    }


def _operation_game_console_command(item: DiscordOperationRun) -> str:
    encrypted = str(item.arguments.get("command_encrypted") or "")
    command = decrypt_credential(encrypted)
    if not command:
        raise DiscordOperationDenied("Game console command is unavailable")
    expected_hash = str(item.arguments.get("command_hash") or "")
    if hashlib.sha256(command.encode()).hexdigest() != expected_hash:
        raise DiscordOperationDenied("Game console command changed after planning")
    return GameConsoleCommandInput(command=command).command


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
        manager: "DiscordBotManager",
        owner_user_id: int,
        message_trigger_mode: str = MESSAGE_TRIGGER_MENTION_ONLY,
    ) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = message_trigger_mode == MESSAGE_TRIGGER_GREETINGS
        super().__init__(intents=intents)
        self.manager = manager
        self.owner_user_id = owner_user_id
        self.message_trigger_mode = message_trigger_mode
        self.tree = app_commands.CommandTree(self)
        self._register_commands()

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
        async with async_session_maker() as db:
            result = await db.execute(
                select(ServerDiscordBinding).where(
                    ServerDiscordBinding.user_id == self.owner_user_id,
                    ServerDiscordBinding.enabled.is_(True),
                )
            )
            bindings = list(result.scalars().all())
        bound_ids = {int(item.guild_id) for item in bindings if item.guild_id}
        guild_ids = {guild.id for guild in self.guilds}
        for guild_id in guild_ids:
            guild = discord.Object(id=guild_id)
            try:
                self.tree.clear_commands(guild=guild)
                if guild_id in bound_ids:
                    self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            except Exception as exc:
                logger.warning(
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
        async with async_session_maker() as db:
            try:
                pairs = await authorized_bindings(
                    db,
                    bot_owner_user_id=self.owner_user_id,
                    guild_id=str(interaction.guild_id),
                    channel_id=str(interaction.channel_id),
                    actor_user_id=str(interaction.user.id),
                    actor_role_ids=_roles(interaction),
                )
            except DiscordAuthorizationDenied:
                return []
        needle = current.casefold().strip()
        return [
            app_commands.Choice(name=server.name[:100], value=str(server.id))
            for _binding, server in pairs
            if not needle or needle in server.name.casefold() or needle in str(server.id)
        ][:25]

    def _register_commands(self) -> None:
        cs2 = app_commands.Group(name="cs2", description="Manage authorized CS2 servers")
        plugin = app_commands.Group(name="plugin", description="Browse and manage plugins")
        console = app_commands.Group(name="console", description="Send confirmed game commands")
        agent = app_commands.Group(name="agent", description="Use the server-scoped AI Agent")

        async def help_command(interaction: discord.Interaction) -> None:
            await self.manager.command_help(self, interaction)

        async def menu_command(interaction: discord.Interaction) -> None:
            await self.manager.command_menu(self, interaction)

        async def status_command(
            interaction: discord.Interaction, server: str | None = None
        ) -> None:
            await self.manager.command_status(self, interaction, server)

        async def start_command(
            interaction: discord.Interaction, server: str | None = None
        ) -> None:
            await self.manager.command_write(self, interaction, "start", server)

        async def stop_command(interaction: discord.Interaction, server: str | None = None) -> None:
            await self.manager.command_write(self, interaction, "stop", server)

        async def restart_command(
            interaction: discord.Interaction, server: str | None = None
        ) -> None:
            await self.manager.command_write(self, interaction, "restart", server)

        async def update_command(
            interaction: discord.Interaction, server: str | None = None
        ) -> None:
            await self.manager.command_write(self, interaction, "update", server)

        async def validate_command(
            interaction: discord.Interaction, server: str | None = None
        ) -> None:
            await self.manager.command_write(self, interaction, "validate", server)

        async def plugin_search(
            interaction: discord.Interaction, query: str, server: str | None = None
        ) -> None:
            await self.manager.command_plugin_search(self, interaction, query, server)

        async def plugin_list(interaction: discord.Interaction, server: str | None = None) -> None:
            await self.manager.command_plugin_list(self, interaction, server)

        async def plugin_install(
            interaction: discord.Interaction, plugin_id: int, server: str | None = None
        ) -> None:
            await self.manager.command_plugin_install(self, interaction, plugin_id, server)

        async def plugin_upgrade(
            interaction: discord.Interaction, plugin_id: int, server: str | None = None
        ) -> None:
            await self.manager.command_plugin_upgrade(self, interaction, plugin_id, server)

        async def console_send(
            interaction: discord.Interaction, command: str, server: str | None = None
        ) -> None:
            await self.manager.command_game_console(self, interaction, command, server)

        async def agent_ask(
            interaction: discord.Interaction, prompt: str, server: str | None = None
        ) -> None:
            await self.manager.command_agent_ask(self, interaction, prompt, server)

        async def agent_reset(interaction: discord.Interaction, server: str | None = None) -> None:
            await self.manager.command_agent_reset(self, interaction, server)

        commands = [
            cs2.command(name="status", description="Show CS2 server status")(status_command),
            cs2.command(name="start", description="Plan and confirm a server start")(start_command),
            cs2.command(name="stop", description="Plan and confirm a server stop")(stop_command),
            cs2.command(name="restart", description="Plan and confirm a server restart")(
                restart_command
            ),
            cs2.command(name="update", description="Plan and confirm a CS2 update")(update_command),
            cs2.command(name="validate", description="Plan and confirm CS2 validation")(
                validate_command
            ),
            plugin.command(name="search", description="Search the plugin market")(plugin_search),
            plugin.command(name="list", description="List managed plugins")(plugin_list),
            plugin.command(name="install", description="Plan a market plugin install")(
                plugin_install
            ),
            plugin.command(name="upgrade", description="Plan a managed plugin upgrade")(
                plugin_upgrade
            ),
            console.command(name="send", description="Confirm and send one game command")(
                console_send
            ),
            agent.command(name="ask", description="Ask the server-scoped AI Agent")(agent_ask),
            agent.command(name="reset", description="Start a new isolated AI context")(agent_reset),
        ]
        cs2.command(name="help", description="Show authorized CS2 Bot commands")(help_command)
        cs2.command(name="menu", description="Open your private CS2 control menu")(menu_command)
        cs2.add_command(plugin)
        cs2.add_command(console)
        cs2.add_command(agent)
        self.tree.add_command(cs2)
        for command in commands:
            if "server" in {parameter.name for parameter in command.parameters}:
                command.autocomplete("server")(self._autocomplete_server)


@dataclass(slots=True)
class _Runtime:
    client: ManagedDiscordClient
    fingerprint: str
    binding_fingerprint: str
    lease_token: str
    client_task: asyncio.Task
    renew_task: asyncio.Task


class DiscordBotManager:
    def __init__(self) -> None:
        self._runtimes: dict[int, _Runtime] = {}
        self._started = False
        self._reconcile_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def configuration_options(self, user_id: int, guild_id: str | None = None) -> dict | None:
        """Return a permission-filtered snapshot from this worker's ready Gateway client."""

        runtime = self._runtimes.get(user_id)
        if runtime is None or not runtime.client.is_ready():
            return None
        client = runtime.client
        guilds = [
            {
                "id": str(guild.id),
                "name": guild.name,
                "icon": str(guild.icon) if guild.icon else None,
            }
            for guild in client.guilds
        ]
        snapshot = {"guilds": guilds, "channels": [], "roles": []}
        if guild_id is None:
            return snapshot
        guild = client.get_guild(int(guild_id))
        if guild is None:
            snapshot["guild_missing"] = True
            return snapshot

        member = guild.me
        seen: set[int] = set()
        channels = []
        for channel in [*guild.channels, *guild.threads]:
            if channel.id in seen:
                continue
            seen.add(channel.id)
            channel_type = int(channel.type.value)
            if channel_type not in DISCORD_COMMAND_CHANNEL_TYPES:
                continue
            if member is not None:
                permissions = channel.permissions_for(member)
                if not (
                    permissions.view_channel
                    and permissions.send_messages
                    and permissions.embed_links
                    and permissions.read_message_history
                ):
                    continue
            channels.append({"id": str(channel.id), "name": channel.name, "type": channel_type})
        snapshot["channels"] = channels
        snapshot["roles"] = [
            {"id": str(role.id), "name": role.name, "position": role.position}
            for role in guild.roles
        ]
        return snapshot

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        await self.reconcile_all()
        self._reconcile_task = asyncio.create_task(self._reconcile_loop())

    async def stop(self) -> None:
        self._started = False
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reconcile_task
            self._reconcile_task = None
        for user_id in list(self._runtimes):
            await self._stop_runtime(user_id, status="disabled")

    async def _reconcile_loop(self) -> None:
        while self._started:
            await asyncio.sleep(RECONCILE_SECONDS)
            try:
                await self.reconcile_all()
            except Exception:
                logger.exception("Discord Bot reconciliation failed")

    async def reconcile_all(self) -> None:
        async with async_session_maker() as db:
            result = await db.execute(select(UserDiscordBot.user_id))
            user_ids = set(result.scalars().all()) | set(self._runtimes)
        for user_id in user_ids:
            await self.reconcile_user(user_id)

    async def reconcile_user(self, user_id: int) -> None:
        if not self._started:
            return
        async with self._lock:
            async with async_session_maker() as db:
                bot = await db.get(UserDiscordBot, user_id)
                user = await db.get(User, user_id)
                binding_result = await db.execute(
                    select(ServerDiscordBinding).where(ServerDiscordBinding.user_id == user_id)
                )
                bindings = list(binding_result.scalars().all())
                should_run = bool(
                    bot and user and user.is_active and bot.enabled and bot.token_encrypted
                )
                encrypted = bot.token_encrypted if bot else None
                message_trigger_mode = (
                    bot.message_trigger_mode if bot else MESSAGE_TRIGGER_MENTION_ONLY
                )
            if not should_run or not encrypted:
                await self._stop_runtime(user_id, status="disabled")
                return
            fingerprint = hashlib.sha256(
                f"{encrypted}\0{message_trigger_mode}".encode()
            ).hexdigest()
            binding_payload = [
                {
                    "server_id": item.server_id,
                    "enabled": item.enabled,
                    "guild_id": item.guild_id,
                    "channels": item.channel_ids,
                    "roles": item.role_ids,
                    "users": item.user_ids,
                    "capabilities": item.capabilities,
                    "invalid_reason": item.invalid_reason,
                }
                for item in sorted(bindings, key=lambda value: value.server_id)
            ]
            binding_fingerprint = hashlib.sha256(
                json.dumps(binding_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            runtime = self._runtimes.get(user_id)
            if runtime is not None and runtime.fingerprint == fingerprint:
                if runtime.binding_fingerprint != binding_fingerprint:
                    runtime.binding_fingerprint = binding_fingerprint
                    await runtime.client.sync_bound_guilds()
                return
            if runtime is not None:
                await self._stop_runtime(user_id, status="restarting")
            lease_token = uuid.uuid4().hex
            lease_key = f"discord_gateway:user:{user_id}"
            acquired = await redis_manager.acquire_lock(lease_key, lease_token, LEASE_TTL_SECONDS)
            if acquired is None:
                await self._update_bot_status(
                    user_id, "redis_unavailable", "Redis lease unavailable; Gateway not started"
                )
                return
            if not acquired:
                return
            try:
                token = decrypt_credential(encrypted)
            except Exception as exc:
                await redis_manager.release_lock(lease_key, lease_token)
                await self._update_bot_status(user_id, "error", str(exc))
                return
            if not token:
                await redis_manager.release_lock(lease_key, lease_token)
                await self._update_bot_status(user_id, "error", "Bot Token unavailable")
                return
            client = ManagedDiscordClient(self, user_id, message_trigger_mode)
            client_task = asyncio.create_task(client.start(token, reconnect=True))
            renew_task = asyncio.create_task(self._renew_lease(user_id, lease_token))
            self._runtimes[user_id] = _Runtime(
                client=client,
                fingerprint=fingerprint,
                binding_fingerprint=binding_fingerprint,
                lease_token=lease_token,
                client_task=client_task,
                renew_task=renew_task,
            )
            client_task.add_done_callback(
                lambda task, uid=user_id: asyncio.create_task(self._client_stopped(uid, task))
            )
            await self._update_bot_status(user_id, "connecting", None)

    async def _renew_lease(self, user_id: int, token: str) -> None:
        key = f"discord_gateway:user:{user_id}"
        while self._started:
            await asyncio.sleep(LEASE_RENEW_SECONDS)
            if not await redis_manager.refresh_lock(key, token, LEASE_TTL_SECONDS):
                logger.error("Discord Gateway lease lost for user %s; closing client", user_id)
                runtime = self._runtimes.get(user_id)
                if runtime is not None and runtime.lease_token == token:
                    self._runtimes.pop(user_id, None)
                    await runtime.client.close()
                    await self._update_bot_status(user_id, "lease_lost", "Redis lease lost")
                return

    async def _stop_runtime(self, user_id: int, *, status: str) -> None:
        runtime = self._runtimes.pop(user_id, None)
        if runtime is None:
            await self._update_bot_status(user_id, status, None)
            return
        runtime.renew_task.cancel()
        with suppress(asyncio.CancelledError):
            await runtime.renew_task
        await runtime.client.close()
        runtime.client_task.cancel()
        await asyncio.gather(runtime.client_task, return_exceptions=True)
        await redis_manager.release_lock(f"discord_gateway:user:{user_id}", runtime.lease_token)
        await self._update_bot_status(user_id, status, None)

    async def _client_stopped(self, user_id: int, task: asyncio.Task) -> None:
        runtime = self._runtimes.get(user_id)
        if runtime is None or runtime.client_task is not task:
            return
        self._runtimes.pop(user_id, None)
        runtime.renew_task.cancel()
        await redis_manager.release_lock(f"discord_gateway:user:{user_id}", runtime.lease_token)
        error = None
        if not task.cancelled():
            with suppress(Exception):
                exception = task.exception()
                if isinstance(exception, discord.PrivilegedIntentsRequired):
                    error = (
                        "Message Content Intent is not enabled for this Bot. Enable it under "
                        "Discord Developer Portal → Bot → Privileged Gateway Intents, or switch "
                        "the friendly-menu trigger to mention-only mode."
                    )
                else:
                    error = str(exception) if exception else None
        await self._update_bot_status(
            user_id,
            "error" if error else "disconnected",
            error or "Discord Gateway disconnected",
        )

    async def _client_ready(self, client: ManagedDiscordClient) -> None:
        await self._update_bot_status(client.owner_user_id, "connected", None, connected=True)
        await client.sync_bound_guilds()

    async def _update_bot_status(
        self, user_id: int, status: str, error: str | None, *, connected: bool = False
    ) -> None:
        async with async_session_maker() as db:
            bot = await db.get(UserDiscordBot, user_id)
            if bot is None:
                return
            bot.connection_status = status
            bot.last_error = _safe_text(error, 1000) if error else None
            if connected:
                bot.last_connected_at = get_current_time()
            db.add(bot)
            await db.commit()

    async def _mark_guild_invalid(self, user_id: int, guild_id: str, reason: str) -> None:
        async with async_session_maker() as db:
            result = await db.execute(
                select(ServerDiscordBinding).where(
                    ServerDiscordBinding.user_id == user_id,
                    ServerDiscordBinding.guild_id == guild_id,
                )
            )
            for binding in result.scalars().all():
                binding.invalid_reason = reason
                db.add(binding)
            await db.commit()

    async def _clear_guild_invalid(self, user_id: int, guild_id: str) -> None:
        async with async_session_maker() as db:
            result = await db.execute(
                select(ServerDiscordBinding).where(
                    ServerDiscordBinding.user_id == user_id,
                    ServerDiscordBinding.guild_id == guild_id,
                    ServerDiscordBinding.invalid_reason.in_(
                        ["bot_not_in_guild", "command_sync_failed", "bot_token_missing"]
                    ),
                )
            )
            for binding in result.scalars().all():
                binding.invalid_reason = None
                db.add(binding)
            await db.commit()

    async def _guild_removed(self, user_id: int, guild_id: str) -> None:
        await self._mark_guild_invalid(user_id, guild_id, "bot_not_in_guild")

    @staticmethod
    def _locale(source: discord.Interaction | discord.Message) -> object:
        locale = getattr(source, "locale", None)
        guild = getattr(source, "guild", None)
        return locale or getattr(guild, "preferred_locale", None) or "en-US"

    async def _authorized_menu_pairs(
        self,
        client: ManagedDiscordClient,
        *,
        guild_id: int | None,
        channel_id: int | None,
        actor_user_id: int,
        actor_role_ids: set[str],
        required_capability: DiscordCapability | None = None,
    ) -> list[tuple[ServerDiscordBinding, Server]]:
        if guild_id is None or channel_id is None:
            return []
        async with async_session_maker() as db:
            pairs = await authorized_bindings(
                db,
                bot_owner_user_id=client.owner_user_id,
                guild_id=str(guild_id),
                channel_id=str(channel_id),
                actor_user_id=str(actor_user_id),
                actor_role_ids=actor_role_ids,
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

    async def handle_message(self, client: ManagedDiscordClient, message: discord.Message) -> None:
        """Publish a short-lived launcher only for authorized bound-channel messages."""
        if (
            message.guild is None
            or message.webhook_id is not None
            or getattr(message.author, "bot", False)
            or client.user is None
        ):
            return
        mentioned = client.user.id in set(getattr(message, "raw_mentions", []))
        normalized = normalize_message_trigger(message.content, client.user.id)
        exact_wake_word = is_exact_wake_word(message.content, client.user.id)
        mention_trigger = mentioned and (not normalized or exact_wake_word)
        greeting = client.message_trigger_mode == MESSAGE_TRIGGER_GREETINGS and exact_wake_word
        if not mention_trigger and not greeting:
            return
        try:
            pairs = await self._authorized_menu_pairs(
                client,
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                actor_user_id=message.author.id,
                actor_role_ids=_member_roles(message.author),
            )
            if not pairs:
                return
            allowed, _retry_after = await redis_manager.hit_rate_limit(
                (
                    f"discord_menu_trigger:{client.owner_user_id}:{message.guild.id}:"
                    f"{message.channel.id}:{message.author.id}"
                ),
                1,
                5,
            )
            if not allowed:
                return
            await message.reply(
                view=launcher_view(self._locale(message)),
                allowed_mentions=discord.AllowedMentions.none(),
                mention_author=False,
                delete_after=300,
                silent=True,
            )
        except Exception:
            logger.exception(
                "Discord friendly-menu trigger failed for owner %s Guild %s channel %s",
                client.owner_user_id,
                message.guild.id,
                message.channel.id,
            )

    async def _menu_pairs_for_interaction(
        self,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        required_capability: DiscordCapability | None = None,
    ) -> list[tuple[ServerDiscordBinding, Server]]:
        return await self._authorized_menu_pairs(
            client,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            actor_user_id=interaction.user.id,
            actor_role_ids=_roles(interaction),
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
        self,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        *,
        issued_at: int | None = None,
        page: int = 0,
    ) -> discord.ui.LayoutView:
        locale = self._locale(interaction)
        pairs = await self._menu_pairs_for_interaction(client, interaction)
        if not pairs:
            return no_access_view(locale)
        issued_at = issued_at or menu_issued_at()
        if len(pairs) == 1:
            binding, server = pairs[0]
            return control_view(
                locale,
                server_id=server.id,
                server_name=server.name,
                capabilities=binding.capabilities or [],
                issued_at=issued_at,
            )
        return server_picker_view(
            locale,
            self._menu_server_payload(pairs),
            issued_at=issued_at,
            page=page,
        )

    async def command_menu(
        self, client: ManagedDiscordClient, interaction: discord.Interaction
    ) -> None:
        try:
            view = await self._private_menu_view(client, interaction)
            await interaction.response.send_message(view=view, ephemeral=True)
        except Exception as exc:
            await self._respond_error(interaction, exc)

    async def _menu_control_view(
        self,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        *,
        issued_at: int,
        server_id: int,
    ) -> discord.ui.LayoutView:
        pairs = await self._menu_pairs_for_interaction(client, interaction)
        for binding, server in pairs:
            if server.id == server_id:
                return control_view(
                    self._locale(interaction),
                    server_id=server.id,
                    server_name=server.name,
                    capabilities=binding.capabilities or [],
                    issued_at=issued_at,
                )
        raise DiscordAuthorizationDenied("Selected server is unavailable or unauthorized")

    async def _resolve_menu_server(
        self,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        server_id: int,
        capability: DiscordCapability,
    ) -> Server:
        pairs = await self._menu_pairs_for_interaction(client, interaction, capability)
        for _binding, server in pairs:
            if server.id == server_id:
                return server
        raise DiscordAuthorizationDenied("Selected server is unavailable or unauthorized")

    async def _menu_expired_response(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            menu_text(self._locale(interaction), "expired"), ephemeral=True
        )

    async def _resolve_server(
        self,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        capability: DiscordCapability,
        server_value: str | None,
    ) -> Server:
        if interaction.guild_id is None or interaction.channel_id is None:
            raise DiscordAuthorizationDenied(
                "Commands are only available in configured Guild channels"
            )
        async with async_session_maker() as db:
            pairs = await authorized_bindings(
                db,
                bot_owner_user_id=client.owner_user_id,
                guild_id=str(interaction.guild_id),
                channel_id=str(interaction.channel_id),
                actor_user_id=str(interaction.user.id),
                actor_role_ids=_roles(interaction),
                required_capability=capability,
            )
            if server_value:
                with suppress(ValueError):
                    server_id = int(server_value)
                    for _binding, server in pairs:
                        if server.id == server_id:
                            return server
                raise DiscordAuthorizationDenied("Selected server is unavailable or unauthorized")
            if len(pairs) == 1:
                return pairs[0][1]
            if not pairs:
                raise DiscordAuthorizationDenied(
                    "No authorized server is available for this command"
                )
            raise DiscordAuthorizationDenied(
                "Multiple servers are available; select one explicitly"
            )

    async def _respond_error(self, interaction: discord.Interaction, exc: Exception) -> None:
        message = _safe_text(str(exc), 1800)
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def command_help(
        self, client: ManagedDiscordClient, interaction: discord.Interaction
    ) -> None:
        try:
            if interaction.guild_id is None or interaction.channel_id is None:
                raise DiscordAuthorizationDenied("Use this command in a configured Guild channel")
            async with async_session_maker() as db:
                pairs = await authorized_bindings(
                    db,
                    bot_owner_user_id=client.owner_user_id,
                    guild_id=str(interaction.guild_id),
                    channel_id=str(interaction.channel_id),
                    actor_user_id=str(interaction.user.id),
                    actor_role_ids=_roles(interaction),
                )
            if not pairs:
                raise DiscordAuthorizationDenied("You are not on the server whitelist")
        except Exception as exc:
            await self._respond_error(interaction, exc)
            return
        await interaction.response.send_message(
            "`/cs2 status|start|stop|restart|update|validate`\n"
            "`/cs2 menu`\n"
            "`/cs2 plugin search|list|install|upgrade`\n"
            "`/cs2 console send`\n"
            "`/cs2 agent ask|reset`\n"
            "Write operations always require a public confirmation by the requester.",
            ephemeral=False,
        )

    async def command_status(
        self,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        server_value: str | None,
    ) -> None:
        try:
            server = await self._resolve_server(
                client, interaction, DiscordCapability.STATUS, server_value
            )
            await interaction.response.defer(ephemeral=False)
            await interaction.edit_original_response(embed=await self._status_embed(server))
        except Exception as exc:
            await self._respond_error(interaction, exc)

    async def _status_embed(self, server: Server) -> discord.Embed:
        from services.a2s_query import a2s_service

        host = server.a2s_query_host or server.host
        port = server.a2s_query_port or server.game_port
        ok, info = await a2s_service.query_server_info(host, port, timeout=5)
        fields = [
            ("Panel status", server.status.value if server.status else "unknown"),
            ("Endpoint", f"{host}:{port}"),
        ]
        if ok and info:
            fields.extend(
                [
                    ("Map", str(info.get("map_name") or "unknown")),
                    (
                        "Players",
                        f"{info.get('player_count', 0)}/{info.get('max_players', 0)}",
                    ),
                ]
            )
        embed = discord.Embed(
            title=server.name,
            description="A2S online" if ok else "A2S unavailable",
            color=discord.Color.green() if ok else discord.Color.orange(),
        )
        for name, value in fields:
            embed.add_field(name=name, value=value, inline=True)
        return embed

    async def _send_confirmation(
        self,
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
        await interaction.edit_original_response(embed=embed, view=view)
        message = await interaction.original_response()
        await self._save_operation_message(operation.id, message.id)

    async def _build_confirmation(
        self,
        interaction: discord.Interaction,
        server: Server,
        action: str,
        capability: DiscordCapability,
        arguments: dict,
        plan: dict,
        *,
        warnings: bool = False,
    ) -> tuple[DiscordOperationRun, discord.Embed, _ConfirmView]:
        async with async_session_maker() as db:
            operation = await create_operation(
                db,
                server=server,
                actor_user_id=str(interaction.user.id),
                actor_role_ids=_roles(interaction),
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
            name="Immutable plan", value=f"```json\n{_safe_text(plan, 900)}\n```", inline=False
        )
        return operation, embed, _ConfirmView(operation.id, warnings=warnings)

    async def _save_operation_message(self, operation_id: str, message_id: int) -> None:
        async with async_session_maker() as db:
            saved = await db.get(DiscordOperationRun, operation_id)
            if saved:
                saved.message_id = str(message_id)
                db.add(saved)
                await db.commit()

    async def _publish_menu_confirmation(
        self,
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
            raise DiscordOperationDenied("Discord did not return the confirmation message")
        await self._save_operation_message(operation.id, message.id)
        await interaction.edit_original_response(
            content=menu_text(self._locale(interaction), "published")
        )

    async def command_write(
        self,
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
                _simple_plan(server, action),
            )
        except Exception as exc:
            await self._respond_error(interaction, exc)

    async def command_game_console(
        self,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        command: str,
        server_value: str | None,
    ) -> None:
        try:
            data = GameConsoleCommandInput(command=command)
            server = await self._resolve_server(
                client, interaction, DiscordCapability.GAME_CONSOLE, server_value
            )
            await interaction.response.defer(ephemeral=False)
            command_hash = hashlib.sha256(data.command.encode()).hexdigest()
            await self._send_confirmation(
                interaction,
                server,
                "game_console",
                DiscordCapability.GAME_CONSOLE,
                {
                    "command_encrypted": encrypt_credential(data.command),
                    "command_hash": command_hash,
                },
                _game_console_plan(server, data.command),
            )
        except Exception as exc:
            await self._respond_error(interaction, exc)

    async def command_plugin_search(
        self,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        query: str,
        server_value: str | None,
    ) -> None:
        try:
            await self._resolve_server(
                client, interaction, DiscordCapability.PLUGIN_BROWSE, server_value
            )
            async with async_session_maker() as db:
                plugins, total = await MarketPlugin.search_plugins(db, search_query=query, limit=10)
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
        self,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        server_value: str | None,
    ) -> None:
        try:
            server = await self._resolve_server(
                client, interaction, DiscordCapability.PLUGIN_BROWSE, server_value
            )
            async with async_session_maker() as db:
                result = await db.execute(
                    select(ManagedPlugin)
                    .where(ManagedPlugin.server_id == server.id)
                    .order_by(ManagedPlugin.display_name.asc())
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

    async def _market_plan(self, server: Server, plugin_id: int) -> tuple[dict, dict, bool]:
        from services.plugin_conflict_service import build_plugin_install_plan

        async with async_session_maker() as db:
            plan = await build_plugin_install_plan(db, server.id, plugin_id, server=server)
        if plan.get("hard_conflicts"):
            raise ValueError("Hard plugin conflict: " + _safe_text(plan["hard_conflicts"], 1000))
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
        self,
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
        self,
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
                await interaction.edit_original_response(
                    content=f"{plan['name']} is already up to date.", embed=None, view=None
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
        self,
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
            await interaction.edit_original_response(
                embed=discord.Embed(title="AI Agent", description="Working…")
            )
            run_id = await ask_discord_agent(
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
        self,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        server_value: str | None,
    ) -> None:
        try:
            server = await self._resolve_server(
                client, interaction, DiscordCapability.AGENT_ASK, server_value
            )
            await reset_discord_conversation(
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

    async def _render_ai_run(self, interaction: discord.Interaction, run_id: str) -> None:
        snapshot = await discord_run_snapshot(run_id)
        if snapshot["status"] == "waiting_approval" and snapshot["tool"]:
            tool = snapshot["tool"]
            embed = discord.Embed(
                title=f"AI confirmation: {tool['name']}",
                description="Only the original requester can confirm. Expires in 15 minutes.",
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="Plan", value=f"```json\n{_safe_text(tool['plan'], 900)}\n```", inline=False
            )
            await interaction.edit_original_response(
                embed=embed, view=_AIConfirmView(run_id, tool["id"])
            )
            return
        color = discord.Color.green() if snapshot["status"] == "completed" else discord.Color.red()
        description = snapshot["message"] or snapshot["error"] or snapshot["status"]
        await interaction.edit_original_response(
            embed=discord.Embed(title="AI Agent", description=_safe_text(description), color=color),
            view=None,
        )

    async def _render_ai_run_message(self, message: discord.WebhookMessage, run_id: str) -> None:
        snapshot = await discord_run_snapshot(run_id)
        if snapshot["status"] == "waiting_approval" and snapshot["tool"]:
            tool = snapshot["tool"]
            embed = discord.Embed(
                title=f"AI confirmation: {tool['name']}",
                description="Only the original requester can confirm. Expires in 15 minutes.",
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="Plan", value=f"```json\n{_safe_text(tool['plan'], 900)}\n```", inline=False
            )
            await message.edit(embed=embed, view=_AIConfirmView(run_id, tool["id"]))
            return
        color = discord.Color.green() if snapshot["status"] == "completed" else discord.Color.red()
        description = snapshot["message"] or snapshot["error"] or snapshot["status"]
        await message.edit(
            embed=discord.Embed(title="AI Agent", description=_safe_text(description), color=color),
            view=None,
        )

    async def _publish_menu_status(self, interaction: discord.Interaction, server: Server) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(
            embed=await self._status_embed(server),
            ephemeral=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await interaction.edit_original_response(
            content=menu_text(self._locale(interaction), "published")
        )

    @staticmethod
    def _plugin_embed(plugin: MarketPlugin | ManagedPlugin) -> discord.Embed:
        if isinstance(plugin, MarketPlugin):
            embed = discord.Embed(
                title=plugin.title,
                description=_safe_text(plugin.description or "No description", 3000),
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

    async def _managed_plugin_view(
        self,
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
        async with async_session_maker() as db:
            total = int(
                await db.scalar(
                    select(func.count())
                    .select_from(ManagedPlugin)
                    .where(ManagedPlugin.server_id == server.id)
                )
                or 0
            )
            pages = max(1, math.ceil(total / PLUGIN_PAGE_SIZE))
            page = min(max(page, 0), pages - 1)
            result = await db.execute(
                select(ManagedPlugin)
                .where(ManagedPlugin.server_id == server.id)
                .order_by(ManagedPlugin.display_name.asc(), ManagedPlugin.id.asc())
                .offset(page * PLUGIN_PAGE_SIZE)
                .limit(PLUGIN_PAGE_SIZE)
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
        return plugin_picker_view(
            locale,
            title=menu_text(locale, "managed_plugins", server=server.name),
            hint=menu_text(locale, "managed_hint"),
            options=options,
            custom_id=f"cs2:menu:managed_pick:{issued_at}:{server.id}:{mode}",
            issued_at=issued_at,
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
        self,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        *,
        issued_at: int,
        server_id: int,
        nonce: str,
        page: int,
    ) -> discord.ui.LayoutView:
        state = await redis_manager.get(self._search_state_key(client, interaction, nonce))
        if not isinstance(state, dict):
            raise DiscordAuthorizationDenied("Plugin search expired; open a new menu")
        query = str(state.get("query") or "").strip()
        mode = str(state.get("mode") or "browse")
        if mode not in {"browse", "install"} or state.get("server_id") != server_id:
            raise DiscordAuthorizationDenied("Plugin search state does not match this menu")
        capability = (
            DiscordCapability.PLUGIN_INSTALL
            if mode == "install"
            else DiscordCapability.PLUGIN_BROWSE
        )
        await self._resolve_menu_server(client, interaction, server_id, capability)
        async with async_session_maker() as db:
            plugins, total = await MarketPlugin.search_plugins(
                db,
                search_query=query,
                skip=max(0, page) * PLUGIN_PAGE_SIZE,
                limit=PLUGIN_PAGE_SIZE,
            )
        pages = max(1, math.ceil(total / PLUGIN_PAGE_SIZE))
        page = min(max(page, 0), pages - 1)
        if page and not plugins:
            async with async_session_maker() as db:
                plugins, _total = await MarketPlugin.search_plugins(
                    db,
                    search_query=query,
                    skip=page * PLUGIN_PAGE_SIZE,
                    limit=PLUGIN_PAGE_SIZE,
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
        return plugin_picker_view(
            locale,
            title=menu_text(locale, "search_results", query=_safe_text(query, 100)),
            hint=menu_text(locale, "search_result_hint"),
            options=options,
            custom_id=f"cs2:menu:market_pick:{issued_at}:{server_id}:{nonce}:{mode}",
            issued_at=issued_at,
            server_id=server_id,
            page=page,
            pages=pages,
            page_kind=f"search_{nonce}",
        )

    async def _menu_search_submit(
        self,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        *,
        issued_at: int,
        server_id: int,
        mode: str,
        query: str,
    ) -> None:
        if mode not in {"browse", "install"}:
            raise DiscordAuthorizationDenied("Invalid plugin search mode")
        capability = (
            DiscordCapability.PLUGIN_INSTALL
            if mode == "install"
            else DiscordCapability.PLUGIN_BROWSE
        )
        await self._resolve_menu_server(client, interaction, server_id, capability)
        await interaction.response.defer(ephemeral=True, thinking=True)
        nonce = uuid.uuid4().hex[:12]
        saved = await redis_manager.set(
            self._search_state_key(client, interaction, nonce),
            {"query": query.strip(), "mode": mode, "server_id": server_id},
            MENU_LIFETIME_SECONDS,
        )
        if not saved:
            raise DiscordAuthorizationDenied("Plugin search state is temporarily unavailable")
        view = await self._market_search_view(
            client,
            interaction,
            issued_at=issued_at,
            server_id=server_id,
            nonce=nonce,
            page=0,
        )
        await interaction.edit_original_response(content=None, view=view)

    async def _menu_console_submit(
        self,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        *,
        server_id: int,
        command: str,
    ) -> None:
        data = GameConsoleCommandInput(command=command)
        server = await self._resolve_menu_server(
            client, interaction, server_id, DiscordCapability.GAME_CONSOLE
        )
        await interaction.response.defer(ephemeral=True, thinking=True)
        command_hash = hashlib.sha256(data.command.encode()).hexdigest()
        await self._publish_menu_confirmation(
            interaction,
            server,
            "game_console",
            DiscordCapability.GAME_CONSOLE,
            {
                "command_encrypted": encrypt_credential(data.command),
                "command_hash": command_hash,
            },
            _game_console_plan(server, data.command),
        )

    async def _menu_agent_submit(
        self,
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
            raise DiscordAuthorizationDenied("Discord did not return the AI progress message")
        try:
            run_id = await ask_discord_agent(
                owner_user_id=client.owner_user_id,
                server_id=server.id,
                actor_user_id=str(interaction.user.id),
                guild_id=str(interaction.guild_id),
                channel_id=str(interaction.channel_id),
                prompt=prompt,
            )
            await self._render_ai_run_message(message, run_id)
        except Exception:
            with suppress(discord.HTTPException):
                await message.edit(
                    embed=discord.Embed(
                        title="AI Agent",
                        description="The request failed. Reopen the menu and try again.",
                        color=discord.Color.red(),
                    ),
                    view=None,
                )
            raise
        await interaction.edit_original_response(
            content=menu_text(self._locale(interaction), "published")
        )

    async def _handle_menu_action(
        self,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        *,
        issued_at: int,
        server_id: int,
        action: str,
    ) -> None:
        capability = action_capability(action)
        if capability is None:
            raise DiscordAuthorizationDenied("Unknown or unavailable menu action")
        server = await self._resolve_menu_server(client, interaction, server_id, capability)
        locale = self._locale(interaction)
        if action in {"plugin_search", "plugin_install"}:
            mode = "install" if action == "plugin_install" else "browse"
            modal = MenuInputModal(
                locale=locale,
                title_key="search_title",
                label_key="search_label",
                placeholder_key="search_placeholder",
                custom_id=f"cs2:menu:search_modal:{issued_at}:{server.id}:{mode}",
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
            modal = MenuInputModal(
                locale=locale,
                title_key="console_title",
                label_key="console_label",
                placeholder_key="console_placeholder",
                custom_id=f"cs2:menu:console_modal:{issued_at}:{server.id}",
                callback=lambda modal_interaction, value: self._menu_console_submit(
                    client, modal_interaction, server_id=server.id, command=value
                ),
                max_length=500,
            )
            await interaction.response.send_modal(modal)
            return
        if action == "agent_ask":
            modal = MenuInputModal(
                locale=locale,
                title_key="agent_title",
                label_key="agent_label",
                placeholder_key="agent_placeholder",
                custom_id=f"cs2:menu:agent_modal:{issued_at}:{server.id}",
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
                _simple_plan(server, action),
            )
            return
        if action == "agent_reset":
            await interaction.response.defer(ephemeral=True, thinking=True)
            await reset_discord_conversation(
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
            await interaction.edit_original_response(content=menu_text(locale, "published"))
            return
        raise DiscordAuthorizationDenied("Unknown or unavailable menu action")

    async def _handle_menu_component(
        self,
        client: ManagedDiscordClient,
        interaction: discord.Interaction,
        parts: list[str],
    ) -> None:
        kind = parts[2] if len(parts) > 2 else ""
        if kind == "open":
            if len(parts) < 4:
                raise DiscordAuthorizationDenied("Invalid menu launcher")
            try:
                launcher_issued_at = int(parts[3])
            except ValueError as exc:
                raise DiscordAuthorizationDenied("Invalid menu timestamp") from exc
            if launcher_is_expired(launcher_issued_at):
                await self._menu_expired_response(interaction)
                return
            view = await self._private_menu_view(client, interaction)
            await interaction.response.send_message(view=view, ephemeral=True)
            return
        if len(parts) < 4:
            raise DiscordAuthorizationDenied("Invalid menu component")
        try:
            issued_at = int(parts[3])
        except ValueError as exc:
            raise DiscordAuthorizationDenied("Invalid menu timestamp") from exc
        if menu_is_expired(issued_at):
            await self._menu_expired_response(interaction)
            return
        if kind == "page":
            page = int(parts[4])
            view = await self._private_menu_view(
                client, interaction, issued_at=issued_at, page=page
            )
            await interaction.response.edit_message(view=view)
            return
        if kind == "server":
            values = (interaction.data or {}).get("values") or []
            server_id = int(values[0])
            view = await self._menu_control_view(
                client, interaction, issued_at=issued_at, server_id=server_id
            )
            await interaction.response.edit_message(view=view)
            return
        if kind == "control":
            server_id = int(parts[4])
            view = await self._menu_control_view(
                client, interaction, issued_at=issued_at, server_id=server_id
            )
            await interaction.response.edit_message(view=view)
            return
        if kind == "action":
            server_id = int(parts[4])
            values = (interaction.data or {}).get("values") or []
            if not values:
                raise DiscordAuthorizationDenied("No menu action was selected")
            await self._handle_menu_action(
                client,
                interaction,
                issued_at=issued_at,
                server_id=server_id,
                action=str(values[0]),
            )
            return
        if kind.startswith("managed_") and kind != "managed_pick":
            mode = kind.removeprefix("managed_")
            if mode not in {"browse", "upgrade"}:
                raise DiscordAuthorizationDenied("Invalid managed plugin mode")
            server_id = int(parts[4])
            page = int(parts[5])
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
        if kind == "managed_pick":
            server_id = int(parts[4])
            mode = parts[5]
            if mode not in {"browse", "upgrade"}:
                raise DiscordAuthorizationDenied("Invalid managed plugin mode")
            values = (interaction.data or {}).get("values") or []
            plugin_id = int(values[0])
            capability = (
                DiscordCapability.PLUGIN_UPGRADE
                if mode == "upgrade"
                else DiscordCapability.PLUGIN_BROWSE
            )
            server = await self._resolve_menu_server(client, interaction, server_id, capability)
            async with async_session_maker() as db:
                plugin = await db.get(ManagedPlugin, plugin_id)
            if plugin is None or plugin.server_id != server.id:
                raise DiscordAuthorizationDenied("Managed plugin is unavailable")
            if mode == "browse":
                await interaction.response.defer(ephemeral=True, thinking=True)
                await interaction.followup.send(embed=self._plugin_embed(plugin), ephemeral=False)
                await interaction.edit_original_response(
                    content=menu_text(self._locale(interaction), "published")
                )
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            from services.plugin_auto_update_service import plugin_auto_update_service

            plan = await plugin_auto_update_service.build_plugin_upgrade_plan(server.id, plugin_id)
            if plan["no_op"]:
                await interaction.edit_original_response(
                    content=f"{plan['name']} is already up to date."
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
        if kind.startswith("search_"):
            nonce = kind.removeprefix("search_")
            server_id = int(parts[4])
            page = int(parts[5])
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
        if kind == "market_pick":
            server_id = int(parts[4])
            nonce = parts[5]
            mode = parts[6]
            if mode not in {"browse", "install"}:
                raise DiscordAuthorizationDenied("Invalid market plugin mode")
            state = await redis_manager.get(self._search_state_key(client, interaction, nonce))
            if (
                not isinstance(state, dict)
                or state.get("server_id") != server_id
                or state.get("mode") != mode
            ):
                raise DiscordAuthorizationDenied("Plugin search state does not match this menu")
            values = (interaction.data or {}).get("values") or []
            plugin_id = int(values[0])
            capability = (
                DiscordCapability.PLUGIN_INSTALL
                if mode == "install"
                else DiscordCapability.PLUGIN_BROWSE
            )
            server = await self._resolve_menu_server(client, interaction, server_id, capability)
            async with async_session_maker() as db:
                plugin = await MarketPlugin.get_by_id(db, plugin_id)
            if plugin is None:
                raise DiscordAuthorizationDenied("Market plugin is unavailable")
            await interaction.response.defer(ephemeral=True, thinking=True)
            if mode == "browse":
                await interaction.followup.send(embed=self._plugin_embed(plugin), ephemeral=False)
                await interaction.edit_original_response(
                    content=menu_text(self._locale(interaction), "published")
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
            await redis_manager.delete(self._search_state_key(client, interaction, nonce))
            return
        raise DiscordAuthorizationDenied("Unknown menu component")

    async def handle_component(
        self,
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
                task = asyncio.create_task(
                    approve_discord_tool(
                        run_id=run_id,
                        tool_run_id=tool_run_id,
                        actor_user_id=str(interaction.user.id),
                        actor_role_ids=_roles(interaction),
                        guild_id=str(interaction.guild_id),
                        channel_id=str(interaction.channel_id),
                    )
                )
                while not task.done():
                    snapshot = await discord_run_snapshot(run_id)
                    progress = snapshot.get("progress") or {}
                    detail = progress.get("snapshot") or {}
                    message = (
                        detail.get("message")
                        or f"{progress.get('tool', 'AI tool')}: {progress.get('status', 'running')}"
                    )
                    with suppress(discord.HTTPException):
                        await interaction.message.edit(
                            embed=discord.Embed(
                                title="AI Agent operation",
                                description=_safe_text(message, 3500),
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

    async def _cancel_operation(self, interaction: discord.Interaction, operation_id: str) -> None:
        async with async_session_maker() as db:
            item = await db.get(DiscordOperationRun, operation_id)
            if item is None or item.status != "pending":
                raise DiscordOperationDenied("Operation is no longer pending")
            if item.actor_user_id != str(interaction.user.id):
                raise DiscordOperationDenied("Only the original requester may cancel")
            item.status = "cancelled"
            item.completed_at = get_current_time()
            db.add(item)
            await db.commit()
        await interaction.response.edit_message(
            embed=discord.Embed(title="Operation cancelled", color=discord.Color.greyple()),
            view=None,
        )

    async def _fresh_plan(self, item: DiscordOperationRun, server: Server) -> dict:
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
            return _game_console_plan(server, _operation_game_console_command(item))
        return _simple_plan(server, item.action)

    async def _confirm_and_execute(
        self, interaction: discord.Interaction, operation_id: str
    ) -> None:
        async with async_session_maker() as db:
            pending = await db.get(DiscordOperationRun, operation_id)
            if pending is None:
                raise DiscordOperationDenied("Operation not found")
            server = await db.get(Server, pending.server_id)
            if server is None:
                raise DiscordOperationDenied("Server not found")
        fresh_plan = await self._fresh_plan(pending, server)
        async with async_session_maker() as db:
            item = await confirm_operation(
                db,
                operation_id=operation_id,
                actor_user_id=str(interaction.user.id),
                actor_role_ids=_roles(interaction),
                fresh_plan=fresh_plan,
            )
            item.status = "running"
            db.add(item)
            await db.commit()
        await interaction.message.edit(
            embed=discord.Embed(title=f"Running {item.action}", description="Starting…"),
            view=None,
        )
        try:
            result = await self._execute_operation(interaction, item)
        except Exception as exc:
            safe_error = _safe_text(str(exc), 1800)
            async with async_session_maker() as db:
                saved = await db.get(DiscordOperationRun, item.id)
                if saved:
                    saved.status = "failed"
                    saved.error = safe_error
                    saved.result = {"success": False, "error": safe_error}
                    saved.completed_at = get_current_time()
                    db.add(saved)
                    await db.commit()
            await interaction.message.edit(
                embed=discord.Embed(
                    title=f"{item.action} failed",
                    description=safe_error,
                    color=discord.Color.red(),
                )
            )
            return
        async with async_session_maker() as db:
            saved = await db.get(DiscordOperationRun, item.id)
            if saved:
                saved.status = "completed" if result.get("success", True) else "failed"
                saved.result = result
                saved.completed_at = get_current_time()
                db.add(saved)
                await db.commit()
        await interaction.message.edit(
            embed=discord.Embed(
                title=f"{item.action} completed",
                description=_safe_text(result),
                color=discord.Color.green() if result.get("success", True) else discord.Color.red(),
            )
        )

    async def _execute_operation(
        self, interaction: discord.Interaction, item: DiscordOperationRun
    ) -> dict:
        async with async_session_maker() as db:
            server = await db.get(Server, item.server_id)
            owner = await db.get(User, item.owner_user_id)
            if server is None or owner is None or not owner.is_active or server.user_id != owner.id:
                raise DiscordOperationDenied("Server ownership is no longer valid")

            async def progress(_event: str, payload: dict) -> None:
                message = payload.get("message") or "Working…"
                with suppress(discord.HTTPException):
                    await interaction.message.edit(
                        embed=discord.Embed(
                            title=f"Running {item.action}", description=_safe_text(message, 3500)
                        )
                    )

            context = ToolContext(
                db=db,
                user=owner,
                server=server,
                emit=progress,
                run_id=f"discord:{item.id}",
                enforce_agent_policy=False,
            )
            if item.action in {"start", "stop", "restart"}:
                spec = TOOLS_BY_NAME["control_server"]
                data = ServerControlInput.model_validate({"action": item.action})
                return await spec.handler(context, data)
            if item.action in {"update", "validate"}:
                spec = TOOLS_BY_NAME["run_server_operation"]
                data = ServerOperationInput.model_validate({"operation": item.action})
                return await spec.handler(context, data)
            if item.action == "plugin_install":
                spec = TOOLS_BY_NAME["apply_plugin_plan"]
                data = ApplyPluginPlanInput.model_validate(item.arguments)
                return await spec.handler(context, data)
            if item.action == "game_console":
                spec = TOOLS_BY_NAME["send_game_console_command"]
                data = GameConsoleCommandInput(command=_operation_game_console_command(item))
                return await spec.handler(context, data)
        if item.action == "plugin_upgrade":
            from services.plugin_auto_update_service import plugin_auto_update_service

            return await plugin_auto_update_service.check_plugin(
                item.server_id, int(item.arguments["plugin_id"])
            )
        raise ValueError(f"Unsupported Discord operation: {item.action}")


discord_bot_manager = DiscordBotManager()
