"""Alembic environment for async MySQL and injected migration connections."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _target_metadata():
    # Importing the model package registers every SQLModel table. Keeping this
    # import in the Alembic environment (rather than revision files) lets future
    # autogeneration see the application schema without making old migrations
    # depend on mutable application models.
    if not getattr(config.cmd_opts, "autogenerate", False):
        return None

    from sqlmodel import SQLModel

    import modules.models  # noqa: F401

    return SQLModel.metadata


target_metadata = _target_metadata()


def _database_url() -> str:
    injected = config.attributes.get("database_url")
    if injected:
        return str(injected)

    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured

    from modules.config import settings

    return settings.mysql_url


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    connectable = async_engine_from_config(
        {"sqlalchemy.url": _database_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(_configure)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    injected_connection = config.attributes.get("connection")
    if injected_connection is not None:
        _configure(injected_connection)
        return
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
