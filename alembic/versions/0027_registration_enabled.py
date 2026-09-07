"""Add the public registration switch to system settings."""

import sqlalchemy as sa

from alembic import op

revision: str = "0027_registration_enabled"
down_revision: str | None = "0026_download_cache_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column(
            "registration_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "registration_enabled")
