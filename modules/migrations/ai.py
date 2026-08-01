"""Create AI assistant and plugin compatibility metadata."""

# ruff: noqa: F403,F405

from .common import *


async def migrate_ai(conn: AsyncConnection) -> None:
    """Create the current AI tables idempotently for existing installations."""
    await conn.run_sync(SQLModel.metadata.create_all)
