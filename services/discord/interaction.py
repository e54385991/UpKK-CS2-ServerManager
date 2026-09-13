"""Discord interaction edit/publish helpers."""

from __future__ import annotations

import inspect

import discord

from services.compat import LateBoundModule

host = LateBoundModule("services.discord_bot_manager")


def _safe_text(value, limit: int = 3900) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = host.json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return host.redact_sensitive_text(text, limit=limit)


def _is_unknown_discord_resource(exc: BaseException) -> bool:
    return (
        isinstance(exc, discord.HTTPException)
        and getattr(exc, "code", None) in host.UNKNOWN_DISCORD_RESOURCE_CODES
    )


def _public_error_text(exc: Exception) -> str:
    if _is_unknown_discord_resource(exc):
        return (
            "That Discord message is no longer available. Reopen the menu or run the command again."
        )
    if isinstance(exc, discord.HTTPException):
        return "Discord rejected this action. Reopen the menu or try again."
    return _safe_text(str(exc), 1800)


async def _edit_interaction_message(interaction: discord.Interaction, **kwargs) -> bool:
    """Edit the targeted interaction message via the interaction token.

    ``Message.edit`` on interaction/webhook cards returns Discord 10008.
    """
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(**kwargs)
        else:
            await interaction.response.edit_message(**kwargs)
        return True
    except discord.HTTPException as exc:
        if not _is_unknown_discord_resource(exc):
            raise
        host.logger.info("Discord interaction message is gone: %s", exc)
        return False


async def _edit_webhook_message(message: object, **kwargs) -> bool:
    edit = getattr(message, "edit", None)
    if not callable(edit):
        return False
    try:
        result = edit(**kwargs)
        if inspect.isawaitable(result):
            await result
        return True
    except discord.HTTPException as exc:
        if not _is_unknown_discord_resource(exc):
            raise
        host.logger.info("Discord webhook message is gone: %s", exc)
        return False


async def _publish_interaction_update(interaction: discord.Interaction, **kwargs) -> bool:
    if await host._edit_interaction_message(interaction, **kwargs):
        return True
    send_kwargs = {
        key: value for key, value in kwargs.items() if key != "view" and value is not None
    }
    if not send_kwargs:
        return False
    try:
        if interaction.response.is_done():
            await interaction.followup.send(**send_kwargs, ephemeral=False)
        else:
            await interaction.response.send_message(**send_kwargs, ephemeral=False)
        return True
    except discord.HTTPException as exc:
        host.logger.info("Unable to publish Discord update: %s", exc)
        return False
