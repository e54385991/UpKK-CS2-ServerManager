"""Persist AI marketplace jobs, installation metadata and GitHub validation."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0022_plugin_ai_imports"
down_revision: str | None = "0021_plugin_framework_other"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("market_plugins", sa.Column("ai_metadata", JSONB(), nullable=True))
    op.add_column("system_settings", sa.Column("github_token_fingerprint", sa.String(64)))
    op.add_column("system_settings", sa.Column("github_token_verification", JSONB()))
    op.create_table(
        "plugin_import_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_key", sa.String(100), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("command", sa.String(1000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("phase", sa.String(40), nullable=False),
        sa.Column("message", sa.String(2000), nullable=False),
        sa.Column("current_repository", sa.String(500)),
        sa.Column("model", sa.String(255)),
        sa.Column("stop_reason", sa.String(40)),
        sa.Column("retry_at", sa.Integer()),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
    )
    op.create_index("ix_plugin_import_jobs_status", "plugin_import_jobs", ["status"])
    op.create_index("ix_plugin_import_jobs_actor_user_id", "plugin_import_jobs", ["actor_user_id"])


def downgrade() -> None:
    op.drop_table("plugin_import_jobs")
    op.drop_column("system_settings", "github_token_verification")
    op.drop_column("system_settings", "github_token_fingerprint")
    op.drop_column("market_plugins", "ai_metadata")
