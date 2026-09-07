"""Shared definitions for the modules/models domain modules."""

# ruff: noqa: F401

import enum
from datetime import datetime
from typing import ClassVar, List, Optional

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from sqlmodel import Column, Field, SQLModel, col, select

# PostgreSQL is the only supported runtime database. Keep the historical
# ``JSON`` name used by model modules while emitting the queryable binary type.
JSON = JSONB

# Stable names are required for deterministic Alembic autogeneration and make
# every constraint addressable by future migrations.
SQLModel.metadata.naming_convention = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def portable_enum(enum_type: type[enum.Enum], *, name: str, length: int | None = None) -> SQLEnum:
    """Store enums as constrained strings instead of PostgreSQL enum types."""
    return SQLEnum(
        enum_type,
        name=name,
        # Omitting length preserves SQLAlchemy's longest-enum-value inference.
        # Explicit None instead means unbounded VARCHAR, which MySQL rejects.
        **({"length": length} if length is not None else {}),
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
    )


DEFAULT_PLUGIN_CONFIG_SOURCE_PATHS = (
    "cs2/game/csgo/addons/counterstrikesharp/configs",
    "cs2/game/csgo/cfg",
)
# Preserve the original singular public constant for integrations that use it.
DEFAULT_PLUGIN_CONFIG_SOURCE_PATH = DEFAULT_PLUGIN_CONFIG_SOURCE_PATHS[0]

Base = SQLModel

__all__ = [
    "enum",
    "datetime",
    "List",
    "Optional",
    "ClassVar",
    "CheckConstraint",
    "ForeignKey",
    "Index",
    "Integer",
    "String",
    "Text",
    "UniqueConstraint",
    "text",
    "SQLEnum",
    "JSONB",
    "AsyncSession",
    "func",
    "Column",
    "Field",
    "SQLModel",
    "select",
    "col",
    "JSON",
    "portable_enum",
    "DEFAULT_PLUGIN_CONFIG_SOURCE_PATHS",
    "DEFAULT_PLUGIN_CONFIG_SOURCE_PATH",
    "Base",
]
