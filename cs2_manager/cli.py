"""Operational command-line entry point.

Usage::

    uv run python -m cs2_manager.cli create-admin \
        --username operator --email operator@example.com --password '...'
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cs2_manager.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    create_admin = commands.add_parser(
        "create-admin",
        help="create an administrator explicitly and idempotently",
    )
    create_admin.add_argument("--username", required=True)
    create_admin.add_argument("--email", required=True)
    password = create_admin.add_mutually_exclusive_group(required=True)
    password.add_argument(
        "--password",
        help="administrator password (prefer --password-prompt to avoid shell history)",
    )
    password.add_argument(
        "--password-prompt",
        action="store_true",
        help="read the administrator password from an interactive hidden prompt",
    )
    return parser


async def _run_create_admin(args: argparse.Namespace) -> int:
    from cs2_manager.features.auth import (
        AdminConflictError,
        AdminCreationStatus,
        create_admin,
    )
    from cs2_manager.infrastructure.migrations import MigrationError, require_database_current
    from modules.database import (
        async_session_maker,
        engine,
    )

    password = (
        getpass.getpass("Administrator password: ") if args.password_prompt else args.password
    )
    if not isinstance(password, str):
        print("create-admin failed: a password is required", file=sys.stderr)
        return 2
    try:
        # Provisioning is intentionally not a migration path. Operators must
        # run the dedicated, advisory-lock protected migration command first.
        await require_database_current(engine)
        status = await create_admin(
            username=args.username,
            email=args.email,
            password=password,
            session_factory=async_session_maker,
        )
    except (AdminConflictError, MigrationError, ValueError) as exc:
        print(f"create-admin failed: {exc}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()

    if status is AdminCreationStatus.CREATED:
        print(f"Administrator {args.username!r} created.")
    else:
        print(f"Administrator {args.username!r} already exists; no changes made.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "create-admin":
        return asyncio.run(_run_create_admin(args))
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
