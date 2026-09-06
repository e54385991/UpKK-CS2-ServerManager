"""Allow a marketplace listing to declare no runtime ("other").

Those listings are exempt from the install-time runtime check and appear in
both marketplace sections.

Revision ID: 0021_plugin_framework_other
Revises: 0020_market_plugin_framework
Create Date: 2026-09-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0021_plugin_framework_other"
down_revision: str | Sequence[str] | None = "0020_market_plugin_framework"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_market_plugins_plugin_framework"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "market_plugins", type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        "market_plugins",
        "framework IN ('COUNTERSTRIKESHARP', 'SWIFTLY', 'OTHER')",
    )


def downgrade() -> None:
    # Fold the runtime-agnostic listings back into the default section so the
    # narrower constraint can be restored.
    op.execute(
        "UPDATE market_plugins SET framework = 'COUNTERSTRIKESHARP' WHERE framework = 'OTHER'"
    )
    op.drop_constraint(CONSTRAINT, "market_plugins", type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        "market_plugins",
        "framework IN ('COUNTERSTRIKESHARP', 'SWIFTLY')",
    )
