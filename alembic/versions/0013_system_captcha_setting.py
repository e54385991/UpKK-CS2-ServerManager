"""Add the administrator-controlled CAPTCHA policy to system settings.

Revision ID: 0013_system_captcha_setting
Revises: 0012_ai_context_window
Create Date: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_system_captcha_setting"
down_revision: str | Sequence[str] | None = "0012_ai_context_window"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column("captcha_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "captcha_enabled")
