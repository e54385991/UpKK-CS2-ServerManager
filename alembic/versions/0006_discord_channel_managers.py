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


def _ensure_boolean_column(table_name: str, column_name: str) -> None:
    op.execute(
        sa.text(
            f"ALTER TABLE {table_name} "
            f"ADD COLUMN IF NOT EXISTS {column_name} BOOLEAN NOT NULL DEFAULT false"
        )
    )
    op.execute(sa.text(f"ALTER TABLE {table_name} ALTER COLUMN {column_name} DROP DEFAULT"))


def upgrade() -> None:
    _ensure_boolean_column("user_discord_bots", "global_allow_channel_managers")
    _ensure_boolean_column("server_discord_bindings", "allow_channel_managers")


def downgrade() -> None:
    op.drop_column("server_discord_bindings", "allow_channel_managers")
    op.drop_column("user_discord_bots", "global_allow_channel_managers")
