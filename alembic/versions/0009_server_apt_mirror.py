"""Persist the operator-chosen apt mirror on each game host.

Revision ID: 0009_server_apt_mirror
Revises: 0008_audit_logs
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_server_apt_mirror"
down_revision: str | Sequence[str] | None = "0008_audit_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column("apt_mirror", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("servers", "apt_mirror")
