"""Discord actor permission helpers."""

from __future__ import annotations

import discord


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


def _has_channel_manage_permission(permissions: object | None) -> bool:
    """Discord Administrator implies every channel permission, including Manage Channels."""

    if permissions is None:
        return False
    return bool(
        getattr(permissions, "manage_channels", False)
        or getattr(permissions, "administrator", False)
    )


def _has_administrator_permission(permissions: object | None) -> bool:
    return bool(permissions is not None and getattr(permissions, "administrator", False))


def _is_guild_owner(source: discord.Interaction | discord.Message) -> bool:
    guild = getattr(source, "guild", None)
    member = getattr(source, "user", None) or getattr(source, "author", None)
    owner_id = getattr(guild, "owner_id", None)
    member_id = getattr(member, "id", None)
    return owner_id is not None and member_id is not None and owner_id == member_id


def _actor_privileges(source: discord.Interaction | discord.Message) -> tuple[bool, bool]:
    """Return `(is_server_administrator, is_channel_manager)`.

    These flags only describe Discord privileges. Binding switches still decide
    whether either flag grants access.
    """

    if _is_guild_owner(source):
        return True, True

    permissions = getattr(source, "permissions", None)
    is_admin = _has_administrator_permission(permissions)
    is_channel_manager = _has_channel_manage_permission(permissions)
    if is_admin and is_channel_manager:
        return True, True

    channel = getattr(source, "channel", None)
    member = getattr(source, "user", None) or getattr(source, "author", None)
    permissions_for = getattr(channel, "permissions_for", None)
    if member is None or not callable(permissions_for):
        return is_admin, is_channel_manager
    try:
        resolved = permissions_for(member)
    except AttributeError, TypeError:
        return is_admin, is_channel_manager
    return (
        is_admin or _has_administrator_permission(resolved),
        is_channel_manager or _has_channel_manage_permission(resolved),
    )


def _is_server_administrator(source: discord.Interaction | discord.Message) -> bool:
    return _actor_privileges(source)[0]


def _is_channel_manager(source: discord.Interaction | discord.Message) -> bool:
    """Return whether the actor can manage the current channel.

    Interaction permission bitfields often set only the administrator flag for
    Discord Administrators, so that flag is treated as Manage Channels.
    """

    return _actor_privileges(source)[1]


def _message_mentions_bot(message: object, bot_user_id: int) -> bool:
    """Prefer the Gateway ``mentions`` array; ``raw_mentions`` needs message content."""

    if bot_user_id in set(getattr(message, "raw_mentions", []) or []):
        return True
    return any(
        getattr(user, "id", None) == bot_user_id for user in getattr(message, "mentions", []) or []
    )
