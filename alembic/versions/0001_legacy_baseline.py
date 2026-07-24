"""Create the pre-Alembic application schema.

Revision ID: 0001_legacy_baseline
Revises:
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_legacy_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def upgrade() -> None:
    # These tables do not reference application-owned tables and can be
    # created before users/servers. The baseline is intentionally static: old
    # revisions must never import mutable model metadata.
    op.create_table(
        "deployment_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deployment_logs_id", "deployment_logs", ["id"])
    op.create_index("ix_deployment_logs_server_id", "deployment_logs", ["server_id"])

    op.create_table(
        "market_plugins",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("github_url", sa.String(length=500), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("version", sa.String(length=50), nullable=True),
        sa.Column(
            "category",
            sa.Enum(
                "GAME_MODE",
                "ENTERTAINMENT",
                "UTILITY",
                "ADMIN",
                "PERFORMANCE",
                "LIBRARY",
                "OTHER",
                name="plugincategory",
            ),
            nullable=False,
        ),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("is_recommended", sa.Boolean(), nullable=False),
        sa.Column("icon_url", sa.String(length=500), nullable=True),
        sa.Column("dependencies", sa.Text(), nullable=True),
        sa.Column("custom_install_path", sa.String(length=255), nullable=True),
        sa.Column("download_count", sa.Integer(), nullable=False),
        sa.Column("install_count", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_market_plugins_github_url",
        "market_plugins",
        ["github_url"],
        unique=True,
    )
    op.create_index("ix_market_plugins_id", "market_plugins", ["id"])
    op.create_index("ix_market_plugins_title", "market_plugins", ["title"])

    op.create_table(
        "monitoring_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_monitoring_logs_created_at", "monitoring_logs", ["created_at"])
    op.create_index("ix_monitoring_logs_id", "monitoring_logs", ["id"])
    op.create_index("ix_monitoring_logs_server_id", "monitoring_logs", ["server_id"])

    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("default_proxy_mode", sa.String(length=50), nullable=False),
        sa.Column("github_proxy_url", sa.String(length=500), nullable=True),
        sa.Column("global_github_token", sa.String(length=255), nullable=True),
        sa.Column("email_enabled", sa.Boolean(), nullable=False),
        sa.Column("email_provider", sa.String(length=50), nullable=False),
        sa.Column("email_from_address", sa.String(length=255), nullable=True),
        sa.Column("email_from_name", sa.String(length=255), nullable=True),
        sa.Column("gmail_credentials_json", sa.Text(), nullable=True),
        sa.Column("gmail_token_json", sa.Text(), nullable=True),
        sa.Column("smtp_host", sa.String(length=255), nullable=True),
        sa.Column("smtp_port", sa.Integer(), nullable=True),
        sa.Column("smtp_username", sa.String(length=255), nullable=True),
        sa.Column("smtp_password", sa.String(length=255), nullable=True),
        sa.Column("smtp_use_tls", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_system_settings_id", "system_settings", ["id"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("api_key", sa.String(length=64), nullable=True),
        sa.Column("steam_api_key", sa.String(length=64), nullable=True),
        sa.Column("github_token", sa.String(length=255), nullable=True),
        sa.Column("s3_enabled", sa.Boolean(), nullable=False),
        sa.Column("s3_endpoint_url", sa.String(length=500), nullable=True),
        sa.Column("s3_region", sa.String(length=100), nullable=True),
        sa.Column("s3_bucket", sa.String(length=255), nullable=True),
        sa.Column("s3_access_key_id", sa.String(length=255), nullable=True),
        sa.Column("s3_secret_access_key", sa.String(length=255), nullable=True),
        sa.Column("s3_prefix", sa.String(length=255), nullable=True),
        sa.Column("s3_use_ssl", sa.Boolean(), nullable=False),
        sa.Column("s3_retention_count", sa.Integer(), nullable=True),
        sa.Column("google_id", sa.String(length=255), nullable=True),
        sa.Column("oauth_provider", sa.String(length=50), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_api_key", "users", ["api_key"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_google_id", "users", ["google_id"], unique=True)
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "initialized_servers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False),
        sa.Column("ssh_user", sa.String(length=100), nullable=False),
        sa.Column("ssh_password", sa.String(length=255), nullable=False),
        sa.Column("game_directory", sa.String(length=500), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_initialized_servers_id", "initialized_servers", ["id"])
    op.create_index("ix_initialized_servers_user_id", "initialized_servers", ["user_id"])

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_password_reset_tokens_id", "password_reset_tokens", ["id"])
    op.create_index(
        "ix_password_reset_tokens_token",
        "password_reset_tokens",
        ["token"],
        unique=True,
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])

    op.create_table(
        "servers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False),
        sa.Column("ssh_user", sa.String(length=100), nullable=False),
        sa.Column(
            "auth_type",
            sa.Enum("PASSWORD", "KEY_FILE", name="authtype"),
            nullable=False,
        ),
        sa.Column("ssh_password", sa.String(length=255), nullable=True),
        sa.Column("ssh_key_path", sa.String(length=500), nullable=True),
        sa.Column("sudo_password", sa.String(length=255), nullable=True),
        sa.Column("game_port", sa.Integer(), nullable=False),
        sa.Column("game_directory", sa.String(length=500), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "DEPLOYING",
                "RUNNING",
                "STOPPED",
                "ERROR",
                "UNKNOWN",
                name="serverstatus",
            ),
            nullable=True,
        ),
        sa.Column("server_name", sa.String(length=255), nullable=False),
        sa.Column("server_password", sa.String(length=255), nullable=True),
        sa.Column("rcon_password", sa.String(length=255), nullable=True),
        sa.Column("steam_account_token", sa.String(length=255), nullable=True),
        sa.Column("default_map", sa.String(length=100), nullable=False),
        sa.Column("max_players", sa.Integer(), nullable=False),
        sa.Column("game_mode", sa.String(length=50), nullable=False),
        sa.Column("game_type", sa.String(length=50), nullable=False),
        sa.Column("additional_parameters", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(length=100), nullable=True),
        sa.Column("client_port", sa.Integer(), nullable=True),
        sa.Column("tv_port", sa.Integer(), nullable=True),
        sa.Column("tv_enable", sa.Boolean(), nullable=False),
        sa.Column("api_key", sa.String(length=64), nullable=True),
        sa.Column("backend_url", sa.String(length=500), nullable=True),
        sa.Column("auto_clear_crash_hours", sa.Integer(), nullable=True),
        sa.Column("last_status_check", sa.DateTime(), nullable=True),
        sa.Column("enable_panel_monitoring", sa.Boolean(), nullable=False),
        sa.Column("monitor_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("auto_restart_on_crash", sa.Boolean(), nullable=False),
        sa.Column("a2s_query_host", sa.String(length=255), nullable=True),
        sa.Column("a2s_query_port", sa.Integer(), nullable=True),
        sa.Column("enable_a2s_monitoring", sa.Boolean(), nullable=False),
        sa.Column("a2s_failure_threshold", sa.Integer(), nullable=False),
        sa.Column("a2s_check_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("current_game_version", sa.String(length=50), nullable=True),
        sa.Column("enable_auto_update", sa.Boolean(), nullable=False),
        sa.Column("update_check_interval_hours", sa.Float(), nullable=False),
        sa.Column("last_update_check", sa.DateTime(), nullable=True),
        sa.Column("last_update_time", sa.DateTime(), nullable=True),
        sa.Column("enable_plugin_auto_update", sa.Boolean(), nullable=False),
        sa.Column("plugin_update_check_interval_hours", sa.Float(), nullable=False),
        sa.Column("last_plugin_update_check", sa.DateTime(), nullable=True),
        sa.Column("map_pool_sync_url", sa.String(length=4096), nullable=True),
        sa.Column("cpu_affinity", sa.String(length=500), nullable=True),
        sa.Column("session_manager", sa.String(length=16), nullable=False),
        sa.Column("github_proxy", sa.String(length=500), nullable=True),
        sa.Column("use_panel_proxy", sa.Boolean(), nullable=False),
        sa.Column("discord_notifications_enabled", sa.Boolean(), nullable=False),
        sa.Column("discord_webhook_url", sa.String(length=1000), nullable=True),
        sa.Column("discord_channel_name", sa.String(length=255), nullable=True),
        sa.Column("discord_notify_auto_updates", sa.Boolean(), nullable=False),
        sa.Column("discord_notify_manual_updates", sa.Boolean(), nullable=False),
        sa.Column("discord_notify_plugin_updates", sa.Boolean(), nullable=False),
        sa.Column("discord_notify_s3_backups", sa.Boolean(), nullable=False),
        sa.Column("discord_notify_crash_restarts", sa.Boolean(), nullable=False),
        sa.Column("discord_crash_restart_min_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("last_ssh_success", sa.DateTime(), nullable=True),
        sa.Column("last_ssh_failure", sa.DateTime(), nullable=True),
        sa.Column("consecutive_ssh_failures", sa.Integer(), nullable=False),
        sa.Column("is_ssh_down", sa.Boolean(), nullable=False),
        sa.Column("enable_ssh_health_monitoring", sa.Boolean(), nullable=False),
        sa.Column("ssh_health_check_interval_hours", sa.Integer(), nullable=False),
        sa.Column("ssh_health_failure_threshold", sa.Integer(), nullable=False),
        sa.Column("last_ssh_health_check", sa.DateTime(), nullable=True),
        sa.Column("ssh_health_status", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("last_deployed", sa.DateTime(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_servers_api_key", "servers", ["api_key"], unique=True)
    op.create_index("ix_servers_id", "servers", ["id"])
    op.create_index("ix_servers_name", "servers", ["name"])
    op.create_index("ix_servers_user_id", "servers", ["user_id"])

    op.create_table(
        "ssh_servers_sudo",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False),
        sa.Column("sudo_user", sa.String(length=100), nullable=False),
        sa.Column("sudo_password", sa.String(length=255), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ssh_servers_sudo_id", "ssh_servers_sudo", ["id"])
    op.create_index("ix_ssh_servers_sudo_user_id", "ssh_servers_sudo", ["user_id"])

    op.create_table(
        "custom_commands",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("target", sa.String(length=30), nullable=False),
        sa.Column("commands", sa.Text(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_custom_commands_id", "custom_commands", ["id"])
    op.create_index("ix_custom_commands_server_id", "custom_commands", ["server_id"])
    op.create_index("ix_custom_commands_user_id", "custom_commands", ["user_id"])

    op.create_table(
        "managed_plugins",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column("source_key", sa.String(length=500), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("repo_url", sa.String(length=500), nullable=True),
        sa.Column("market_plugin_id", sa.Integer(), nullable=True),
        sa.Column("framework_key", sa.String(length=100), nullable=True),
        sa.Column("installed_release_id", sa.String(length=100), nullable=True),
        sa.Column("installed_version", sa.String(length=100), nullable=False),
        sa.Column("latest_version", sa.String(length=100), nullable=True),
        sa.Column("asset_glob", sa.String(length=500), nullable=True),
        sa.Column("custom_install_path", sa.String(length=255), nullable=True),
        sa.Column("exclude_dirs", sa.JSON(), nullable=False),
        sa.Column("exclude_files", sa.JSON(), nullable=False),
        sa.Column("auto_update_enabled", sa.Boolean(), nullable=False),
        sa.Column("backup_before_update", sa.Boolean(), nullable=False),
        sa.Column("restart_after_update", sa.Boolean(), nullable=False),
        sa.Column("last_check_at", sa.DateTime(), nullable=True),
        sa.Column("last_update_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(length=30), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["market_plugin_id"],
            ["market_plugins.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "server_id",
            "source_type",
            "source_key",
            name="uq_managed_plugin_source",
        ),
    )
    op.create_index("ix_managed_plugins_server_id", "managed_plugins", ["server_id"])

    op.create_table(
        "plugin_config_sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("relative_path", sa.String(length=1000), nullable=False),
        sa.Column("path_hash", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("server_id", "path_hash", name="uq_plugin_config_source_path"),
    )
    op.create_index(
        "ix_plugin_config_sources_server_id",
        "plugin_config_sources",
        ["server_id"],
    )

    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("schedule_type", sa.String(length=50), nullable=False),
        sa.Column("schedule_value", sa.String(length=255), nullable=False),
        sa.Column("last_run", sa.DateTime(), nullable=True),
        sa.Column("next_run", sa.DateTime(), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False),
        sa.Column("last_status", sa.String(length=50), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheduled_tasks_id", "scheduled_tasks", ["id"])
    op.create_index("ix_scheduled_tasks_server_id", "scheduled_tasks", ["server_id"])


def downgrade() -> None:
    # Drop in dependency order. Indexes are removed with their owning tables.
    op.drop_table("scheduled_tasks")
    op.drop_table("plugin_config_sources")
    op.drop_table("managed_plugins")
    op.drop_table("custom_commands")
    op.drop_table("ssh_servers_sudo")
    op.drop_table("servers")
    op.drop_table("password_reset_tokens")
    op.drop_table("initialized_servers")
    op.drop_table("users")
    op.drop_table("system_settings")
    op.drop_table("monitoring_logs")
    op.drop_table("market_plugins")
    op.drop_table("deployment_logs")
