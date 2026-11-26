"""Persist marketplace release selection and verified synchronization state."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0038_market_release_sync"
down_revision: str | Sequence[str] | None = "0037_personal_game_commands"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("market_plugins", sa.Column("release_asset_glob", sa.String(500)))
    op.add_column("market_plugins", sa.Column("release_sync", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column("market_plugins", "release_sync")
    op.drop_column("market_plugins", "release_asset_glob")
