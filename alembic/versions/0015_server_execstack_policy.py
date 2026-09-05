"""Add configurable execstack targets and operation triggers."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_server_execstack_policy"
down_revision: str | Sequence[str] | None = "0014_server_plugin_compatibility"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_TARGETS = '["counterstrikesharp/bin/linuxsteamrt64/counterstrikesharp.so"]'


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column(
            "execstack_fix_on_restart", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.add_column(
        "servers",
        sa.Column(
            "execstack_fix_on_framework", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.add_column(
        "servers",
        sa.Column(
            "execstack_fix_on_game_update", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.add_column(
        "servers",
        sa.Column(
            "execstack_fix_targets",
            sa.JSON(),
            nullable=False,
            server_default=sa.text(f"'{DEFAULT_TARGETS}'"),
        ),
    )
    op.alter_column("servers", "execstack_fix_on_restart", server_default=None)
    op.alter_column("servers", "execstack_fix_on_framework", server_default=None)
    op.alter_column("servers", "execstack_fix_on_game_update", server_default=None)
    op.alter_column("servers", "execstack_fix_targets", server_default=None)


def downgrade() -> None:
    op.drop_column("servers", "execstack_fix_targets")
    op.drop_column("servers", "execstack_fix_on_game_update")
    op.drop_column("servers", "execstack_fix_on_framework")
    op.drop_column("servers", "execstack_fix_on_restart")
