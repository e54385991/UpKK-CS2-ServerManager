"""Helpers for portable server configuration bundles."""

from __future__ import annotations

from typing import Any

from modules.models import Server
from modules.schemas.servers import ServerConfigEntry

# These are persisted settings that can be recreated on another panel. IDs,
# ownership, generated API keys, health counters, timestamps, and live status
# are deliberately excluded from the transfer format.
TRANSFERABLE_SERVER_FIELDS = (
    "name",
    "host",
    "ssh_port",
    "ssh_user",
    "auth_type",
    "ssh_password",
    "ssh_key_path",
    "sudo_password",
    "game_port",
    "game_directory",
    "server_name",
    "server_password",
    "rcon_password",
    "steam_account_token",
    "default_map",
    "max_players",
    "game_mode",
    "game_type",
    "additional_parameters",
    "ip_address",
    "client_port",
    "tv_port",
    "tv_enable",
    "backend_url",
    "auto_clear_crash_hours",
    "enable_panel_monitoring",
    "monitor_interval_seconds",
    "auto_restart_on_crash",
    "a2s_query_host",
    "a2s_query_port",
    "enable_a2s_monitoring",
    "a2s_failure_threshold",
    "a2s_check_interval_seconds",
    "enable_auto_update",
    "update_check_interval_hours",
    "enable_plugin_auto_update",
    "plugin_update_check_interval_hours",
    "cpu_affinity",
    "session_manager",
    "github_proxy",
    "use_panel_proxy",
    "map_pool_sync_url",
    "discord_notifications_enabled",
    "discord_webhook_url",
    "discord_channel_name",
    "discord_notify_auto_updates",
    "discord_notify_manual_updates",
    "discord_notify_plugin_updates",
    "discord_notify_s3_backups",
    "discord_notify_crash_restarts",
    "discord_crash_restart_min_interval_minutes",
    "enable_ssh_health_monitoring",
    "ssh_health_check_interval_hours",
    "ssh_health_failure_threshold",
    "description",
)

SECRET_SERVER_FIELDS = frozenset(
    {
        "ssh_password",
        "sudo_password",
        "server_password",
        "rcon_password",
        "steam_account_token",
        "discord_webhook_url",
    }
)


def server_to_config_entry(server: Server, *, include_secrets: bool) -> ServerConfigEntry:
    """Convert a database model into a portable, optionally redacted entry."""
    values: dict[str, Any] = {
        field: getattr(server, field, None) for field in TRANSFERABLE_SERVER_FIELDS
    }
    values["redacted_fields"] = []
    if not include_secrets:
        values["redacted_fields"] = sorted(SECRET_SERVER_FIELDS)
        for field in SECRET_SERVER_FIELDS:
            values[field] = None
    return ServerConfigEntry.model_validate(values)


def config_entry_values(
    entry: ServerConfigEntry,
    *,
    preserve_redacted: bool = False,
) -> dict[str, Any]:
    """Return database fields from an entry, optionally omitting redactions."""
    redacted = set(entry.redacted_fields) if preserve_redacted else set()
    return {
        field: getattr(entry, field)
        for field in TRANSFERABLE_SERVER_FIELDS
        if field not in redacted
    }
