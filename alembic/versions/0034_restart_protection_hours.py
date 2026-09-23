"""Add a per-server restart-loop protection window.

Revision ID: 0034_restart_protection_hours
Revises: 0033_webauthn_credentials
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0034_restart_protection_hours"
down_revision: str | Sequence[str] | None = "0033_webauthn_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column(
            "restart_protection_hours",
            sa.Integer(),
            nullable=False,
            server_default="2",
        ),
    )


def downgrade() -> None:
    op.drop_column("servers", "restart_protection_hours")
