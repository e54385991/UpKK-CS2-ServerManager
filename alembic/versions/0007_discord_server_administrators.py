"""Add explicit Discord server-administrator authorization.

Revision ID: 0007_discord_server_administrators
Revises: 0006_discord_channel_managers
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_discord_server_administrators"
down_revision: str | Sequence[str] | None = "0006_discord_channel_managers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_discord_bots",
        sa.Column(
            "global_allow_server_administrators",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "server_discord_bindings",
        sa.Column(
            "allow_server_administrators",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.alter_column("user_discord_bots", "global_allow_server_administrators", server_default=None)
    op.alter_column("server_discord_bindings", "allow_server_administrators", server_default=None)


def downgrade() -> None:
    op.drop_column("server_discord_bindings", "allow_server_administrators")
    op.drop_column("user_discord_bots", "global_allow_server_administrators")
