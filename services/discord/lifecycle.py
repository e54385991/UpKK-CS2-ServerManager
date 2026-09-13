"""Discord bot manager mixin: DiscordLifecycleMixin."""

from __future__ import annotations

import asyncio
from contextlib import suppress

import discord

from modules.models import (
    ServerDiscordBinding,
    User,
    UserDiscordBot,
)
from services.compat import LateBoundModule
from services.discord.client import ManagedDiscordClient, _Runtime
from services.discord.types import Manager

host = LateBoundModule("services.discord_bot_manager")


class DiscordLifecycleMixin:
    def configuration_options(
        self: Manager, user_id: int, guild_id: str | None = None
    ) -> dict | None:
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
            if channel_type not in host.DISCORD_COMMAND_CHANNEL_TYPES:
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

    async def start(self: Manager) -> None:
        if self._started:
            return
        self._started = True
        await self.reconcile_all()
        self._reconcile_task = host.asyncio.create_task(self._reconcile_loop())

    async def stop(self: Manager) -> None:
        self._started = False
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reconcile_task
            self._reconcile_task = None
        for user_id in list(self._runtimes):
            await self._stop_runtime(user_id, status="disabled")

    async def _reconcile_loop(self: Manager) -> None:
        while self._started:
            await host.asyncio.sleep(host.RECONCILE_SECONDS)
            try:
                await self.reconcile_all()
            except Exception:
                host.logger.exception("Discord Bot reconciliation failed")

    async def reconcile_all(self: Manager) -> None:
        async with host.async_session_maker() as db:
            result = await db.execute(host.select(UserDiscordBot.user_id))
            user_ids = set(result.scalars().all()) | set(self._runtimes)
        for user_id in user_ids:
            await self.reconcile_user(user_id)

    async def reconcile_user(self: Manager, user_id: int) -> None:
        if not self._started:
            return
        async with self._lock:
            async with host.async_session_maker() as db:
                bot = await db.get(UserDiscordBot, user_id)
                user = await db.get(User, user_id)
                binding_result = await db.execute(
                    host.select(ServerDiscordBinding).where(ServerDiscordBinding.user_id == user_id)
                )
                bindings = list(binding_result.scalars().all())
                should_run = bool(
                    bot and user and user.is_active and bot.enabled and bot.token_encrypted
                )
                encrypted = bot.token_encrypted if bot else None
                message_trigger_mode = (
                    bot.message_trigger_mode if bot else host.MESSAGE_TRIGGER_MENTION_ONLY
                )
            if not should_run or not encrypted:
                await self._stop_runtime(user_id, status="disabled")
                return
            fingerprint = host.hashlib.sha256(
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
            binding_fingerprint = host.hashlib.sha256(
                host.json.dumps(binding_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            runtime = self._runtimes.get(user_id)
            if runtime is not None and runtime.fingerprint == fingerprint:
                if runtime.binding_fingerprint != binding_fingerprint:
                    runtime.binding_fingerprint = binding_fingerprint
                    await runtime.client.sync_bound_guilds()
                return
            if runtime is not None:
                await self._stop_runtime(user_id, status="restarting")
            lease_token = host.uuid.uuid4().hex
            lease_key = f"discord_gateway:user:{user_id}"
            acquired = await host.redis_manager.acquire_lock(
                lease_key, lease_token, host.LEASE_TTL_SECONDS
            )
            if acquired is None:
                await self._update_bot_status(
                    user_id, "redis_unavailable", "Redis lease unavailable; Gateway not started"
                )
                return
            if not acquired:
                return
            try:
                token = host.decrypt_credential(encrypted)
            except Exception as exc:
                await host.redis_manager.release_lock(lease_key, lease_token)
                await self._update_bot_status(user_id, "error", str(exc))
                return
            if not token:
                await host.redis_manager.release_lock(lease_key, lease_token)
                await self._update_bot_status(user_id, "error", "Bot Token unavailable")
                return
            client = ManagedDiscordClient(self, user_id, message_trigger_mode)
            client_task = host.asyncio.create_task(client.start(token, reconnect=True))
            renew_task = host.asyncio.create_task(self._renew_lease(user_id, lease_token))
            self._runtimes[user_id] = _Runtime(
                client=client,
                fingerprint=fingerprint,
                binding_fingerprint=binding_fingerprint,
                lease_token=lease_token,
                client_task=client_task,
                renew_task=renew_task,
            )
            client_task.add_done_callback(
                lambda task, uid=user_id: host.asyncio.create_task(self._client_stopped(uid, task))
            )
            await self._update_bot_status(user_id, "connecting", None)

    async def _renew_lease(self: Manager, user_id: int, token: str) -> None:
        key = f"discord_gateway:user:{user_id}"
        while self._started:
            await host.asyncio.sleep(host.LEASE_RENEW_SECONDS)
            if not await host.redis_manager.refresh_lock(key, token, host.LEASE_TTL_SECONDS):
                host.logger.error("Discord Gateway lease lost for user %s; closing client", user_id)
                runtime = self._runtimes.get(user_id)
                if runtime is not None and runtime.lease_token == token:
                    self._runtimes.pop(user_id, None)
                    await runtime.client.close()
                    await self._update_bot_status(user_id, "lease_lost", "Redis lease lost")
                return

    async def _stop_runtime(self: Manager, user_id: int, *, status: str) -> None:
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
        await host.redis_manager.release_lock(
            f"discord_gateway:user:{user_id}", runtime.lease_token
        )
        await self._update_bot_status(user_id, status, None)

    async def _client_stopped(self: Manager, user_id: int, task: asyncio.Task) -> None:
        runtime = self._runtimes.get(user_id)
        if runtime is None or runtime.client_task is not task:
            return
        self._runtimes.pop(user_id, None)
        runtime.renew_task.cancel()
        await host.redis_manager.release_lock(
            f"discord_gateway:user:{user_id}", runtime.lease_token
        )
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

    async def _client_ready(self: Manager, client: ManagedDiscordClient) -> None:
        await self._update_bot_status(client.owner_user_id, "connected", None, connected=True)
        await client.sync_bound_guilds()

    async def _update_bot_status(
        self: Manager, user_id: int, status: str, error: str | None, *, connected: bool = False
    ) -> None:
        async with host.async_session_maker() as db:
            bot = await db.get(UserDiscordBot, user_id)
            if bot is None:
                return
            bot.connection_status = status
            bot.last_error = host._safe_text(error, 1000) if error else None
            if connected:
                bot.last_connected_at = host.get_current_time()
            db.add(bot)
            await db.commit()

    async def _mark_guild_invalid(self: Manager, user_id: int, guild_id: str, reason: str) -> None:
        async with host.async_session_maker() as db:
            result = await db.execute(
                host.select(ServerDiscordBinding).where(
                    ServerDiscordBinding.user_id == user_id,
                    ServerDiscordBinding.guild_id == guild_id,
                )
            )
            for binding in result.scalars().all():
                binding.invalid_reason = reason
                db.add(binding)
            await db.commit()

    async def _clear_guild_invalid(self: Manager, user_id: int, guild_id: str) -> None:
        async with host.async_session_maker() as db:
            result = await db.execute(
                host.select(ServerDiscordBinding).where(
                    ServerDiscordBinding.user_id == user_id,
                    ServerDiscordBinding.guild_id == guild_id,
                    host.col(ServerDiscordBinding.invalid_reason).in_(
                        ["bot_not_in_guild", "command_sync_failed", "bot_token_missing"]
                    ),
                )
            )
            for binding in result.scalars().all():
                binding.invalid_reason = None
                db.add(binding)
            await db.commit()

    async def _guild_removed(self: Manager, user_id: int, guild_id: str) -> None:
        await self._mark_guild_invalid(user_id, guild_id, "bot_not_in_guild")
