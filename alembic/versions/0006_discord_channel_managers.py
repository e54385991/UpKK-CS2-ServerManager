"""Add explicit Discord channel-manager authorization.

Revision ID: 0006_discord_channel_managers
Revises: 0005_discord_global_binding
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_discord_channel_managers"
down_revision: str | Sequence[str] | None = "0005_discord_global_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_discord_bots",
        sa.Column(
            "global_allow_channel_managers",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "server_discord_bindings",
        sa.Column("allow_channel_managers", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.alter_column("user_discord_bots", "global_allow_channel_managers", server_default=None)
    op.alter_column("server_discord_bindings", "allow_channel_managers", server_default=None)


def downgrade() -> None:
    op.drop_column("server_discord_bindings", "allow_channel_managers")
    op.drop_column("user_discord_bots", "global_allow_channel_managers")
