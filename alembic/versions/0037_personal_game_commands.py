"""Persist account-owned game-command registries; null retains existing defaults."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0037_personal_game_commands"
down_revision: str | Sequence[str] | None = "0036_cs2_version_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("automation_commands", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "automation_commands")
