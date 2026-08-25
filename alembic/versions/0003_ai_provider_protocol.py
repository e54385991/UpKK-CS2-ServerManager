"""Add selectable AI provider API protocol.

Revision ID: 0003_ai_provider_protocol
Revises: 0002_discord_bot_agent_policy
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_ai_provider_protocol"
down_revision: str | Sequence[str] | None = "0002_discord_bot_agent_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table_name in ("ai_system_settings", "user_ai_settings"):
        op.add_column(
            table_name,
            sa.Column(
                "api_protocol",
                sa.String(length=32),
                nullable=False,
                server_default="chat_completions",
            ),
        )
        op.alter_column(table_name, "api_protocol", server_default=None)


def downgrade() -> None:
    op.drop_column("user_ai_settings", "api_protocol")
    op.drop_column("ai_system_settings", "api_protocol")
