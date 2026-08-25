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


def _ensure_boolean_column(table_name: str, column_name: str) -> None:
    op.execute(
        sa.text(
            f"ALTER TABLE {table_name} "
            f"ADD COLUMN IF NOT EXISTS {column_name} BOOLEAN NOT NULL DEFAULT false"
        )
    )
    op.execute(sa.text(f"ALTER TABLE {table_name} ALTER COLUMN {column_name} DROP DEFAULT"))


def upgrade() -> None:
    _ensure_boolean_column("user_discord_bots", "global_allow_server_administrators")
    _ensure_boolean_column("server_discord_bindings", "allow_server_administrators")


def downgrade() -> None:
    op.drop_column("server_discord_bindings", "allow_server_administrators")
    op.drop_column("user_discord_bots", "global_allow_server_administrators")
