"""Per-user SteamCMD unexpected-exit retry budget (default 20).

Revision ID: 0010_user_steamcmd_max_retries
Revises: 0009_server_apt_mirror
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_user_steamcmd_max_retries"
down_revision: str | Sequence[str] | None = "0009_server_apt_mirror"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "steamcmd_max_retries",
            sa.Integer(),
            nullable=False,
            server_default="20",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "steamcmd_max_retries")
