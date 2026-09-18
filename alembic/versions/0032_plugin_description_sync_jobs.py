"""Persist throttled marketplace description sync jobs.

Revision ID: 0032_description_sync_jobs
Revises: 0031_google_oauth_client_id
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0032_description_sync_jobs"
down_revision: str | Sequence[str] | None = "0031_google_oauth_client_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plugin_description_sync_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_key", sa.String(100), nullable=False, unique=True),
        sa.Column("scope_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("framework", sa.String(32)),
        sa.Column("overwrite", sa.Boolean(), nullable=False),
        sa.Column("command", sa.String(1000), nullable=False),
        sa.Column("target_ids", sa.JSON(), nullable=False),
        sa.Column("cursor", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("updated", sa.Integer(), nullable=False),
        sa.Column("unchanged", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("phase", sa.String(40), nullable=False),
        sa.Column("message", sa.String(2000), nullable=False),
        sa.Column("current_plugin_id", sa.Integer()),
        sa.Column("current_plugin_title", sa.String(255)),
        sa.Column("current_github_url", sa.String(500)),
        sa.Column("stop_reason", sa.String(40)),
        sa.Column("retry_at", sa.Integer()),
        sa.Column("items", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_plugin_description_sync_jobs_actor_user_id",
        "plugin_description_sync_jobs",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_plugin_description_sync_jobs_scope_key",
        "plugin_description_sync_jobs",
        ["scope_key"],
    )
    op.create_index(
        "ix_plugin_description_sync_jobs_status",
        "plugin_description_sync_jobs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_table("plugin_description_sync_jobs")
