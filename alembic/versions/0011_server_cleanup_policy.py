"""Per-server automatic log and system-junk cleanup policy.

Revision ID: 0011_server_cleanup_policy
Revises: 0010_user_steamcmd_max_retries
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_server_cleanup_policy"
down_revision: str | Sequence[str] | None = "0010_user_steamcmd_max_retries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column(
            "cleanup_auto_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "servers",
        sa.Column(
            "cleanup_retain_days",
            sa.Integer(),
            nullable=False,
            server_default="7",
        ),
    )
    op.add_column(
        "servers",
        sa.Column("cleanup_targets", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("servers", "cleanup_targets")
    op.drop_column("servers", "cleanup_retain_days")
    op.drop_column("servers", "cleanup_auto_enabled")
