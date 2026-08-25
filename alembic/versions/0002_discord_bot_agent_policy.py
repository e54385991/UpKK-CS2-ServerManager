"""Add Discord Gateway Bot and server AI authorization state.

Revision ID: 0002_discord_bot_agent_policy
Revises: 0001_postgresql_baseline
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_discord_bot_agent_policy"
down_revision: str | Sequence[str] | None = "0001_postgresql_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_discord_bots",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_encrypted", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("application_id", sa.String(length=20), nullable=True),
        sa.Column("bot_user_id", sa.String(length=20), nullable=True),
        sa.Column("username", sa.String(length=100), nullable=True),
        sa.Column("discriminator", sa.String(length=8), nullable=True),
        sa.Column("connection_status", sa.String(length=32), nullable=False),
        sa.Column("last_connected_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_discord_bots_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_discord_bots"),
    )
    op.create_index(
        "ix_user_discord_bots_application_id",
        "user_discord_bots",
        ["application_id"],
        unique=True,
    )
    op.create_index(
        "ix_user_discord_bots_bot_user_id",
        "user_discord_bots",
        ["bot_user_id"],
        unique=True,
    )

    op.create_table(
        "server_discord_bindings",
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("guild_id", sa.String(length=20), nullable=True),
        sa.Column(
            "channel_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "role_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "user_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("response_visibility", sa.String(length=16), nullable=False),
        sa.Column("invalid_reason", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
            name="fk_server_discord_bindings_server_id_servers",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_server_discord_bindings_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("server_id", name="pk_server_discord_bindings"),
    )
    op.create_index(
        "ix_server_discord_bindings_guild_id",
        "server_discord_bindings",
        ["guild_id"],
        unique=False,
    )
    op.create_index(
        "ix_server_discord_bindings_user_id",
        "server_discord_bindings",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "server_agent_policies",
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
            name="fk_server_agent_policies_server_id_servers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("server_id", name="pk_server_agent_policies"),
    )
    op.execute(
        """
        INSERT INTO server_agent_policies (server_id, enabled, capabilities)
        SELECT id, TRUE,
               '["inspect_status","read_logs_files","browse_plan_plugins"]'::jsonb
        FROM servers
        ON CONFLICT (server_id) DO NOTHING
        """
    )

    op.create_table(
        "discord_operation_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.String(length=20), nullable=False),
        sa.Column("guild_id", sa.String(length=20), nullable=False),
        sa.Column("channel_id", sa.String(length=20), nullable=False),
        sa.Column("message_id", sa.String(length=20), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column(
            "required_capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("plan_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_discord_operation_runs_owner_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
            name="fk_discord_operation_runs_server_id_servers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_discord_operation_runs"),
    )
    op.create_index(
        "ix_discord_operation_runs_actor_created",
        "discord_operation_runs",
        ["actor_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_discord_operation_runs_actor_user_id",
        "discord_operation_runs",
        ["actor_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_discord_operation_runs_expires_at",
        "discord_operation_runs",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_discord_operation_runs_owner_user_id",
        "discord_operation_runs",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_discord_operation_runs_server_created",
        "discord_operation_runs",
        ["server_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_discord_operation_runs_server_id",
        "discord_operation_runs",
        ["server_id"],
        unique=False,
    )
    op.create_index(
        "ix_discord_operation_runs_status",
        "discord_operation_runs",
        ["status"],
        unique=False,
    )

    op.add_column(
        "ai_conversations",
        sa.Column("source", sa.String(length=16), server_default="web", nullable=False),
    )
    op.add_column(
        "ai_conversations", sa.Column("external_actor_id", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "ai_conversations", sa.Column("discord_guild_id", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "ai_conversations", sa.Column("discord_channel_id", sa.String(length=20), nullable=True)
    )
    op.create_index(
        "ix_ai_conversations_external_actor_id",
        "ai_conversations",
        ["external_actor_id"],
        unique=False,
    )
    op.create_index("ix_ai_conversations_source", "ai_conversations", ["source"], unique=False)
    op.alter_column("ai_conversations", "source", server_default=None)

    op.add_column(
        "ai_runs", sa.Column("source", sa.String(length=16), server_default="web", nullable=False)
    )
    op.add_column("ai_runs", sa.Column("external_actor_id", sa.String(length=20), nullable=True))
    op.create_index("ix_ai_runs_source", "ai_runs", ["source"], unique=False)
    op.alter_column("ai_runs", "source", server_default=None)

    op.add_column(
        "ai_tool_runs", sa.Column("approved_actor_type", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "ai_tool_runs",
        sa.Column("approved_external_actor_id", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_tool_runs", "approved_external_actor_id")
    op.drop_column("ai_tool_runs", "approved_actor_type")
    op.drop_index("ix_ai_runs_source", table_name="ai_runs")
    op.drop_column("ai_runs", "external_actor_id")
    op.drop_column("ai_runs", "source")
    op.drop_index("ix_ai_conversations_source", table_name="ai_conversations")
    op.drop_index("ix_ai_conversations_external_actor_id", table_name="ai_conversations")
    op.drop_column("ai_conversations", "discord_channel_id")
    op.drop_column("ai_conversations", "discord_guild_id")
    op.drop_column("ai_conversations", "external_actor_id")
    op.drop_column("ai_conversations", "source")
    op.drop_table("discord_operation_runs")
    op.drop_table("server_agent_policies")
    op.drop_table("server_discord_bindings")
    op.drop_table("user_discord_bots")
