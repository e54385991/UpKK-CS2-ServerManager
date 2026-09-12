"""Add the administrator-controlled panel monitoring switch.

Revision ID: 0029_panel_monitoring_enabled
Revises: 0028_plugin_description_i18n
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029_panel_monitoring_enabled"
down_revision: str | Sequence[str] | None = "0028_plugin_description_i18n"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column(
            "panel_monitoring_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "panel_monitoring_enabled")
