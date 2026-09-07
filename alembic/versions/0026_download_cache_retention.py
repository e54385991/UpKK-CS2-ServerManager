"""Add automatic retention limits for the reusable plugin download cache."""

import sqlalchemy as sa

from alembic import op

revision: str = "0026_download_cache_retention"
down_revision: str | None = "0025_ai_conversation_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column(
            "plugin_download_cache_max_age_days",
            sa.Integer(),
            nullable=False,
            server_default="30",
        ),
    )
    op.add_column(
        "system_settings",
        sa.Column(
            "plugin_download_cache_max_megabytes",
            sa.Integer(),
            nullable=False,
            server_default="4096",
        ),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "plugin_download_cache_max_megabytes")
    op.drop_column("system_settings", "plugin_download_cache_max_age_days")
