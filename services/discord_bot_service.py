"""Discord REST validation for Bot credentials and Guild configuration."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from modules.http_helper import http_helper
from services.ai_security import redact_sensitive_text

DISCORD_API_BASE = "https://discord.com/api/v10"
MINIMUM_BOT_PERMISSIONS = 1024 | 2048 | 16384 | 65536


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
    roles = await _get(token, f"/guilds/{guild_id}/roles")
    if not isinstance(channels, list) or not isinstance(roles, list):
        raise DiscordBotAPIError("Discord returned invalid channel or role data")
    return (
        [item for item in channels if isinstance(item, dict) and item.get("id")],
        [item for item in roles if isinstance(item, dict) and item.get("id")],
    )
