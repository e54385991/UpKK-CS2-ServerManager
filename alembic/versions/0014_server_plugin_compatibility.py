"""Store Linux release detection and execstack compatibility preference."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_server_plugin_compatibility"
down_revision: str | Sequence[str] | None = "0013_system_captcha_setting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("servers", sa.Column("os_id", sa.String(length=32), nullable=True))
    op.add_column("servers", sa.Column("os_version", sa.String(length=32), nullable=True))
    op.add_column("servers", sa.Column("clear_execstack_override", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("servers", "clear_execstack_override")
    op.drop_column("servers", "os_version")
    op.drop_column("servers", "os_id")
