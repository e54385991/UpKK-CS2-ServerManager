"""Remember Steam's advertised CS2 version changes."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0036_cs2_version_state"
down_revision: str | Sequence[str] | None = "0035_announcements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cs2_version_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("advertised_version", sa.String(length=50), nullable=True),
        sa.Column("version_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("id = 1", name="cs2_version_state_singleton"),
        sa.PrimaryKeyConstraint("id", name="pk_cs2_version_state"),
    )
    op.execute(sa.text("INSERT INTO cs2_version_state (id) VALUES (1)"))


def downgrade() -> None:
    op.drop_table("cs2_version_state")
