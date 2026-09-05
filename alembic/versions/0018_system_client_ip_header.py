"""Add the administrator-controlled source IP header to system settings.

Revision ID: 0018_system_client_ip_header
Revises: 0017_execstack_game_update_off
Create Date: 2026-09-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_system_client_ip_header"
down_revision: str | Sequence[str] | None = "0017_execstack_game_update_off"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_CLIENT_IP_HEADER = "X-Forwarded-For"


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column(
            "client_ip_header",
            sa.String(length=64),
            nullable=True,
            server_default=sa.text(f"'{DEFAULT_CLIENT_IP_HEADER}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "client_ip_header")
