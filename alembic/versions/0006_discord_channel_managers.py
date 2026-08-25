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
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name not in existing:
        op.add_column(
            table_name,
            sa.Column(column_name, sa.Boolean(), nullable=False, server_default="false"),
        )
    op.alter_column(table_name, column_name, server_default=None)


def upgrade() -> None:
    _ensure_boolean_column("user_discord_bots", "global_allow_channel_managers")
    _ensure_boolean_column("server_discord_bindings", "allow_channel_managers")


def downgrade() -> None:
    op.drop_column("server_discord_bindings", "allow_channel_managers")
    op.drop_column("user_discord_bots", "global_allow_channel_managers")
