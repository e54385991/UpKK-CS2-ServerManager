"""Shared definitions for the modules/models domain modules."""

# ruff: noqa: F401

import enum
from datetime import datetime
from typing import List, Optional

from sqlalchemy import CHAR, JSON, ForeignKey, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from sqlmodel import Column, Field, SQLModel, select

DEFAULT_PLUGIN_CONFIG_SOURCE_PATHS = (
    "cs2/game/csgo/addons/counterstrikesharp/configs",
    "cs2/game/csgo/cfg",
)
# Preserve the original singular public constant for integrations that use it.
DEFAULT_PLUGIN_CONFIG_SOURCE_PATH = DEFAULT_PLUGIN_CONFIG_SOURCE_PATHS[0]

Base = SQLModel

__all__ = [name for name in globals() if not name.startswith("__")]
