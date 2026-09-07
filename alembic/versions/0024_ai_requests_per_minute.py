"""Add shared AI provider RPM limit."""

import sqlalchemy as sa

from alembic import op

revision: str = "0024_ai_requests_per_minute"
down_revision: str | None = "0023_plugin_download_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_system_settings",
        sa.Column("requests_per_minute", sa.Integer(), nullable=False, server_default="60"),
    )


def downgrade() -> None:
    op.drop_column("ai_system_settings", "requests_per_minute")
