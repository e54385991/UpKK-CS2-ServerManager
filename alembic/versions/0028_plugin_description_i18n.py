"""Store optional language-specific descriptions for AI marketplace imports."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0028_plugin_description_i18n"
down_revision: str | None = "0027_registration_enabled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("market_plugins", sa.Column("description_i18n", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("market_plugins", "description_i18n")
