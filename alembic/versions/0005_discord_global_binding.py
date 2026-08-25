"""Add the per-user Discord binding template.

Revision ID: 0005_discord_global_binding
Revises: 0004_discord_friendly_menu
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_discord_global_binding"
down_revision: str | Sequence[str] | None = "0004_discord_friendly_menu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    empty_json = sa.text("'[]'::jsonb")
    op.add_column(
        "user_discord_bots",
        sa.Column(
            "global_binding_configured", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column(
        "user_discord_bots",
        sa.Column("global_binding_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "user_discord_bots",
        sa.Column("global_guild_id", sa.String(length=20), nullable=True),
    )
    for column_name in (
        "global_channel_ids",
        "global_role_ids",
        "global_user_ids",
        "global_capabilities",
    ):
        op.add_column(
            "user_discord_bots",
            sa.Column(
                column_name,
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=empty_json,
            ),
        )
    op.alter_column("user_discord_bots", "global_binding_configured", server_default=None)
    op.alter_column("user_discord_bots", "global_binding_enabled", server_default=None)
    for column_name in (
        "global_channel_ids",
        "global_role_ids",
        "global_user_ids",
        "global_capabilities",
    ):
        op.alter_column("user_discord_bots", column_name, server_default=None)


def downgrade() -> None:
    for column_name in (
        "global_capabilities",
        "global_user_ids",
        "global_role_ids",
        "global_channel_ids",
        "global_guild_id",
        "global_binding_enabled",
        "global_binding_configured",
    ):
        op.drop_column("user_discord_bots", column_name)
