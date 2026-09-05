"""Make the game deploy/update/validate execstack trigger opt-in.

Revision 0015 shipped the trigger enabled for every server, so the patchelf fix
ran on deploy/update/validate without anyone asking for it. Flip the column
default to false and clear the existing rows, which all carry that unintended
default; operators re-enable it per server on the Additional fixes page.

Revision ID: 0017_execstack_game_update_off
Revises: 0016_cleanup_targets_jsonb
Create Date: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_execstack_game_update_off"
down_revision: str | Sequence[str] | None = "0016_cleanup_targets_jsonb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "servers",
        "execstack_fix_on_game_update",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.false(),
    )
    op.execute(sa.text("UPDATE servers SET execstack_fix_on_game_update = false"))
    op.alter_column("servers", "execstack_fix_on_game_update", server_default=None)


def downgrade() -> None:
    op.alter_column(
        "servers",
        "execstack_fix_on_game_update",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.true(),
    )
    op.execute(sa.text("UPDATE servers SET execstack_fix_on_game_update = true"))
    op.alter_column("servers", "execstack_fix_on_game_update", server_default=None)
