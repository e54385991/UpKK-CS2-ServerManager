"""Persist the rolling AI conversation summary used by context compaction."""

import sqlalchemy as sa

from alembic import op

revision: str = "0025_ai_conversation_summary"
down_revision: str | None = "0024_ai_requests_per_minute"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_conversations", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("ai_conversations", sa.Column("summary_message_id", sa.Integer(), nullable=True))
    op.add_column(
        "ai_conversations",
        sa.Column("summary_tokens", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("ai_conversations", "summary_tokens")
    op.drop_column("ai_conversations", "summary_message_id")
    op.drop_column("ai_conversations", "summary")
