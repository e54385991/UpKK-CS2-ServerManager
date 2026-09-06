"""Split the plugin marketplace into CounterStrikeSharp and SwiftlyS2 sections.

Existing listings were all written for the CounterStrikeSharp stack, so the
backfill keeps them in that section.

Revision ID: 0020_market_plugin_framework
Revises: 0019_system_console_log_level
Create Date: 2026-09-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_market_plugin_framework"
down_revision: str | Sequence[str] | None = "0019_system_console_log_level"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_PLUGIN_FRAMEWORK = "COUNTERSTRIKESHARP"


def upgrade() -> None:
    op.add_column(
        "market_plugins",
        sa.Column(
            "framework",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text(f"'{DEFAULT_PLUGIN_FRAMEWORK}'"),
        ),
    )
    op.create_check_constraint(
        "ck_market_plugins_plugin_framework",
        "market_plugins",
        "framework IN ('COUNTERSTRIKESHARP', 'SWIFTLY')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_market_plugins_plugin_framework", "market_plugins", type_="check")
    op.drop_column("market_plugins", "framework")
