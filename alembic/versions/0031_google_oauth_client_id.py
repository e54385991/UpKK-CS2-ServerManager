"""Store the Google sign-in OAuth client ID in system settings.

Revision ID: 0031_google_oauth_client_id
Revises: 0030_audit_log_retention_days
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031_google_oauth_client_id"
down_revision: str | Sequence[str] | None = "0030_audit_log_retention_days"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column("google_client_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "google_client_id")
