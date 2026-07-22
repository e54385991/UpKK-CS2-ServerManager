"""Create current metadata before applying legacy alterations."""

# ruff: noqa: F403,F405

from .common import *


async def migrate_bootstrap(conn: AsyncConnection) -> None:
    """Create current metadata before applying legacy alterations."""
    await conn.run_sync(SQLModel.metadata.create_all)
