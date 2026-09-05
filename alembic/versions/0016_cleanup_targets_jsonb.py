"""Normalize the server cleanup target list to PostgreSQL JSONB.

Revision ID: 0016_cleanup_targets_jsonb
Revises: 0015_server_execstack_policy
Create Date: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016_cleanup_targets_jsonb"
down_revision: str | Sequence[str] | None = "0015_server_execstack_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    jsonb_type = postgresql.JSONB(astext_type=sa.Text())
    op.alter_column(
        "servers",
        "cleanup_targets",
        existing_type=sa.JSON(),
        type_=jsonb_type,
        existing_nullable=True,
        postgresql_using="cleanup_targets::jsonb",
    )
    op.alter_column(
        "servers",
        "execstack_fix_targets",
        existing_type=sa.JSON(),
        type_=jsonb_type,
        existing_nullable=False,
        postgresql_using="execstack_fix_targets::jsonb",
    )


def downgrade() -> None:
    jsonb_type = postgresql.JSONB(astext_type=sa.Text())
    op.alter_column(
        "servers",
        "cleanup_targets",
        existing_type=jsonb_type,
        type_=sa.JSON(),
        existing_nullable=True,
        postgresql_using="cleanup_targets::json",
    )
    op.alter_column(
        "servers",
        "execstack_fix_targets",
        existing_type=jsonb_type,
        type_=sa.JSON(),
        existing_nullable=False,
        postgresql_using="execstack_fix_targets::json",
    )
