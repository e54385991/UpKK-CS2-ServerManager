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
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name not in existing:
        op.add_column(
            table_name,
            sa.Column(column_name, sa.Boolean(), nullable=False, server_default="false"),
        )
    op.alter_column(table_name, column_name, server_default=None)


def upgrade() -> None:
    _ensure_boolean_column("user_discord_bots", "global_allow_server_administrators")
    _ensure_boolean_column("server_discord_bindings", "allow_server_administrators")


def downgrade() -> None:
    op.drop_column("server_discord_bindings", "allow_server_administrators")
    op.drop_column("user_discord_bots", "global_allow_server_administrators")
