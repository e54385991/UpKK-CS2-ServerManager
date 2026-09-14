"""Add administrator-controlled audit log retention.

Revision ID: 0030_audit_log_retention_days
Revises: 0029_panel_monitoring_enabled
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030_audit_log_retention_days"
down_revision: str | Sequence[str] | None = "0029_panel_monitoring_enabled"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column(
            "audit_log_retention_days",
            sa.Integer(),
            nullable=False,
            server_default="30",
        ),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "audit_log_retention_days")
