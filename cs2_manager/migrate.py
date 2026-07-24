"""Dedicated database migration command used before production startup.

Usage::

    uv run python -m cs2_manager.migrate upgrade
    uv run python -m cs2_manager.migrate check
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from cs2_manager.infrastructure.migrations import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    MigrationError,
    get_migration_status,
    migrate_database,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cs2_manager.migrate")
    parser.add_argument(
        "--database-url",
        help="override MYSQL_* settings with an async SQLAlchemy URL",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    upgrade = commands.add_parser("upgrade", help="upgrade the database to Alembic head")
    upgrade.add_argument(
        "--lock-timeout",
        type=int,
        default=DEFAULT_LOCK_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="maximum wait for the MySQL advisory lock",
    )
    commands.add_parser("check", help="exit non-zero unless the database is at head")
    return parser


def _database_url(override: str | None) -> str:
    if override:
        return override
    from modules.config import settings

    return settings.mysql_url


async def _run(args: argparse.Namespace) -> int:
    database_url = _database_url(args.database_url)
    if args.command == "upgrade":
        status = await migrate_database(
            database_url,
            lock_timeout_seconds=args.lock_timeout,
        )
        print(f"Database upgraded to {','.join(status.current_revisions)}.")
        return 0

    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        status = await get_migration_status(engine)
    finally:
        await engine.dispose()
    current = ",".join(status.current_revisions) or "unversioned"
    expected = ",".join(status.head_revisions)
    if status.is_current:
        print(f"Database revision is current ({current}).")
        return 0
    print(f"Database revision is {current}; expected {expected}.", file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except (MigrationError, ValueError) as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
