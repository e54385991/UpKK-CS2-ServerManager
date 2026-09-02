"""Add an auditable AI context-window preset to the singleton settings row.

Revision ID: 0012_ai_context_window
Revises: 0011_server_cleanup_policy
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_ai_context_window"
down_revision: str | Sequence[str] | None = "0011_server_cleanup_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_system_settings",
        sa.Column(
            "context_window_tokens",
            sa.Integer(),
            nullable=False,
            server_default="262144",
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_system_settings", "context_window_tokens")
