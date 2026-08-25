"""Operational database status and migration command line interface."""

from __future__ import annotations

import argparse
import asyncio
import json

from .config import settings
from .database import engine
from .database_migrations import DatabaseMigrationError, database_status, upgrade_database


async def _run(command: str) -> int:
    try:
        if command == "upgrade":
            status = await upgrade_database(
                engine,
                lock_timeout_seconds=settings.DB_MIGRATION_LOCK_TIMEOUT_SECONDS,
            )
        else:
            status = await database_status(engine)
        print(
            json.dumps(
                {
                    "server_version_num": status.server_version_num,
                    "current_heads": status.current_heads,
                    "code_heads": status.code_heads,
                    "is_current": status.is_current,
                },
                ensure_ascii=False,
            )
        )
        return 0 if command == "status" or status.is_current else 1
    except DatabaseMigrationError as exc:
        print(f"database migration error: {exc}")
        return 1
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the PostgreSQL application schema")
    parser.add_argument("command", choices=("status", "check", "upgrade"))
    args = parser.parse_args()
    return asyncio.run(_run(args.command))


if __name__ == "__main__":
    raise SystemExit(main())
