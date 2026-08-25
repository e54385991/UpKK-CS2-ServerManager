"""Add Discord friendly-menu message trigger mode.

Revision ID: 0004_discord_friendly_menu
Revises: 0003_ai_provider_protocol
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_discord_friendly_menu"
down_revision: str | Sequence[str] | None = "0003_ai_provider_protocol"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_discord_bots",
        sa.Column(
            "message_trigger_mode",
            sa.String(length=32),
            nullable=False,
            server_default="mention_only",
        ),
    )
    op.alter_column("user_discord_bots", "message_trigger_mode", server_default=None)
    op.create_check_constraint(
        "ck_user_discord_bots_message_trigger_mode",
        "user_discord_bots",
        "message_trigger_mode IN ('mention_only', 'mention_and_greetings')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_user_discord_bots_message_trigger_mode",
        "user_discord_bots",
        type_="check",
    )
    op.drop_column("user_discord_bots", "message_trigger_mode")
