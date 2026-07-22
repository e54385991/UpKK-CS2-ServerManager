"""Shared types and metadata helpers for ordered legacy migrations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlmodel import SQLModel

MigrationCallable = Callable[[AsyncConnection], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class MigrationStep:
    name: str
    migrate: MigrationCallable


async def table_exists(conn: AsyncConnection, table: str) -> bool:
    result = await conn.execute(
        text(
            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
        ),
        {"table": table},
    )
    return result.fetchone() is not None


async def column_exists(conn: AsyncConnection, table: str, column: str) -> bool:
    result = await conn.execute(
        text(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = :table AND COLUMN_NAME = :column"
        ),
        {"table": table, "column": column},
    )
    return result.fetchone() is not None


__all__ = [
    "AsyncConnection",
    "MigrationStep",
    "SQLModel",
    "column_exists",
    "table_exists",
    "text",
]
