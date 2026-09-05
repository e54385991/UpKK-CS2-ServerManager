"""Add the administrator-controlled console log level to system settings.

Revision ID: 0019_system_console_log_level
Revises: 0018_system_client_ip_header
Create Date: 2026-09-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_system_console_log_level"
down_revision: str | Sequence[str] | None = "0018_system_client_ip_header"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_CONSOLE_LOG_LEVEL = "ERROR"


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column(
            "log_level",
            sa.String(length=16),
            nullable=True,
            server_default=sa.text(f"'{DEFAULT_CONSOLE_LOG_LEVEL}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "log_level")
