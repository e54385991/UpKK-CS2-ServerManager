"""Add configurable panel proxy plugin download cache."""

import sqlalchemy as sa

from alembic import op

revision: str = "0023_plugin_download_cache"
down_revision: str | None = "0022_plugin_ai_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column(
            "plugin_download_cache_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "system_settings", sa.Column("plugin_download_cache_path", sa.String(1000), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("system_settings", "plugin_download_cache_path")
    op.drop_column("system_settings", "plugin_download_cache_enabled")
