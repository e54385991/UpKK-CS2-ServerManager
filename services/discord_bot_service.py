"""Discord REST validation for Bot credentials and Guild configuration."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

from modules.http_helper import http_helper
from services.ai_security import redact_sensitive_text

DISCORD_API_BASE = "https://discord.com/api/v10"
MINIMUM_BOT_PERMISSIONS = 1024 | 2048 | 16384 | 65536
# Guild text, voice text chat, announcement, threads, stage text chat,
# forum, and media channels. Categories and channels without a message surface
# cannot receive application-command responses.
DISCORD_COMMAND_CHANNEL_TYPES = frozenset({0, 2, 5, 10, 11, 12, 13, 15, 16})
# Forum and media parents require creating a post instead of a plain channel
# message, so they are deliberately excluded from proactive launcher pushes.
DISCORD_MENU_PUSH_CHANNEL_TYPES = frozenset({0, 2, 5, 10, 11, 12, 13})
DISCORD_COMPONENTS_V2_FLAG = 1 << 15
DISCORD_SUPPRESS_NOTIFICATIONS_FLAG = 1 << 12

logger = logging.getLogger(__name__)


class DiscordBotAPIError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DiscordBotIdentity:
    application_id: str
    bot_user_id: str
    username: str
    discriminator: str | None


def build_invite_url(application_id: str | None) -> str | None:
    if not application_id:
        return None
    query = urlencode(
        {
            "client_id": application_id,
            "permissions": str(MINIMUM_BOT_PERMISSIONS),
            "scope": "bot applications.commands",
        }
    )
    return f"https://discord.com/oauth2/authorize?{query}"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bot {token}", "User-Agent": "UpKK-CS2-ServerManager/1"}


async def _get(token: str, path: str):
    success, payload, error = await http_helper.get(
        f"{DISCORD_API_BASE}{path}", headers=_headers(token), timeout=15
    )
    if not success:
        raise DiscordBotAPIError(
            redact_sensitive_text(error or "Discord API request failed", limit=500)
        )
    return payload


async def test_bot_token(token: str) -> DiscordBotIdentity:
    user = await _get(token, "/users/@me")
    application = await _get(token, "/oauth2/applications/@me")
    if not isinstance(user, dict) or not user.get("bot"):
        raise DiscordBotAPIError("The credential does not belong to a Discord Bot user")
    if not isinstance(application, dict):
        raise DiscordBotAPIError("Discord Application metadata is unavailable")
    return DiscordBotIdentity(
        application_id=str(application["id"]),
        bot_user_id=str(user["id"]),
        username=str(user.get("username") or "Discord Bot"),
        discriminator=str(user.get("discriminator")) if user.get("discriminator") else None,
    )


async def list_guilds(token: str) -> list[dict]:
    payload = await _get(token, "/users/@me/guilds")
    if not isinstance(payload, list):
        raise DiscordBotAPIError("Discord returned an invalid Guild list")
    return [item for item in payload if isinstance(item, dict) and item.get("id")]


async def get_guild_options(token: str, guild_id: str) -> tuple[list[dict], list[dict]]:
    guilds = await list_guilds(token)
    if guild_id not in {str(item["id"]) for item in guilds}:
        raise DiscordBotAPIError("The Bot is not a member of the selected Guild")
    channels = await _get(token, f"/guilds/{guild_id}/channels")
    active_threads = await _get(token, f"/guilds/{guild_id}/threads/active")
    roles = await _get(token, f"/guilds/{guild_id}/roles")
    if (
        not isinstance(channels, list)
        or not isinstance(active_threads, dict)
        or not isinstance(active_threads.get("threads"), list)
        or not isinstance(roles, list)
    ):
        raise DiscordBotAPIError("Discord returned invalid channel or role data")
    channel_items = [*channels, *active_threads["threads"]]
    return (
        [item for item in channel_items if isinstance(item, dict) and item.get("id")],
        [item for item in roles if isinstance(item, dict) and item.get("id")],
    )


async def get_guild_locale(token: str, guild_id: str) -> str:
    payload = await _get(token, f"/guilds/{guild_id}")
    if not isinstance(payload, dict) or str(payload.get("id")) != guild_id:
        raise DiscordBotAPIError("Discord returned invalid Guild metadata")
    return str(payload.get("preferred_locale") or "en-US")


async def send_menu_launcher(token: str, channel_id: str, locale: str) -> tuple[str, int]:
    """Send one Components V2 launcher through REST for multi-worker safety."""

    from services.discord_menu_ui import launcher_view, menu_issued_at

    issued_at = menu_issued_at()
    payload = {
        "components": launcher_view(locale, issued_at=issued_at).to_components(),
        "allowed_mentions": {"parse": []},
        "flags": DISCORD_COMPONENTS_V2_FLAG | DISCORD_SUPPRESS_NOTIFICATIONS_FLAG,
        "nonce": uuid.uuid4().hex[:25],
        "enforce_nonce": True,
    }
    success, response, error = await http_helper.post(
        f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
        headers=_headers(token),
        json=payload,
        timeout=15,
    )
    if not success or not isinstance(response, dict) or not response.get("id"):
        raise DiscordBotAPIError(
            redact_sensitive_text(error or "Discord did not return the menu message", limit=500)
        )
    return str(response["id"]), issued_at


async def delete_menu_launcher_after(
    token: str,
    channel_id: str,
    message_id: str,
    *,
    delay_seconds: int = 300,
) -> None:
    """Best-effort visual cleanup; the component timestamp remains authoritative."""

    await asyncio.sleep(delay_seconds)
    success, _payload, error = await http_helper.make_request(
        "DELETE",
        f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}",
        headers=_headers(token),
        timeout=15,
    )
    if not success and "HTTP 404" not in str(error):
        logger.warning(
            "Failed to delete expired Discord menu launcher in channel %s: %s",
            channel_id,
            redact_sensitive_text(error or "unknown Discord error", limit=300),
        )
