"""Compatibility ORM models for the pre-v1 catalog/settings endpoints.

The active API uses the split ``MarketPlugin``/``SystemSettings`` models.  A
few integrations still import the historical names, so these small models
remain available without coupling the modern services to the old routes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel, col, select

from .common import func
from .plugins import PluginCategory


class Plugin(SQLModel, table=False):
    id: int = Field(default=None, primary_key=True)
    name: str = Field(max_length=255)
    display_name: str = Field(max_length=255)
    description: Optional[str] = None
    category: PluginCategory = Field(default=PluginCategory.OTHER)
    version: str = Field(default="unknown", max_length=100)
    download_url: str = Field(max_length=1000)
    author: Optional[str] = None
    homepage: Optional[str] = None
    dependencies: Optional[str] = None
    install_path: str = Field(default="addons/counterstrikesharp/plugins")
    config_required: bool = False
    enabled: bool = True
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": func.now()}
    )
    updated_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": func.now(), "onupdate": func.now()}
    )

    @classmethod
    async def get_by_category(
        cls, session: AsyncSession, category: PluginCategory, *, skip: int = 0, limit: int = 20
    ) -> list["Plugin"]:
        result = await session.execute(
            select(cls)
            .where(col(cls.category) == category, col(cls.enabled).is_(True))
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    @classmethod
    async def get_all_enabled(
        cls, session: AsyncSession, *, skip: int = 0, limit: int = 20
    ) -> list["Plugin"]:
        result = await session.execute(
            select(cls).where(col(cls.enabled).is_(True)).offset(skip).limit(limit)
        )
        return list(result.scalars().all())

    @classmethod
    async def count_by_category(
        cls, session: AsyncSession, category: Optional[PluginCategory]
    ) -> int:
        from sqlalchemy import func as sqlfunc

        query = select(sqlfunc.count()).select_from(cls).where(col(cls.enabled).is_(True))
        if category is not None:
            query = query.where(col(cls.category) == category)
        result = await session.execute(query)
        return int(result.scalar() or 0)


class InstalledPlugin(SQLModel, table=False):
    id: int = Field(default=None, primary_key=True)
    server_id: int = Field(index=True)
    plugin_id: int = Field(index=True)
    installed_version: str = Field(default="unknown", max_length=100)
    # Historical route names retained alongside the normalized field.
    version: Optional[str] = None
    custom_download_url: Optional[str] = None
    config_data: Optional[str] = None
    installed_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": func.now()}
    )

    @classmethod
    async def get_by_server(cls, session: AsyncSession, server_id: int) -> list["InstalledPlugin"]:
        result = await session.execute(select(cls).where(col(cls.server_id) == server_id))
        return list(result.scalars().all())

    @classmethod
    async def get_by_server_and_plugin(
        cls, session: AsyncSession, server_id: int, plugin_id: int
    ) -> Optional["InstalledPlugin"]:
        result = await session.execute(
            select(cls).where(col(cls.server_id) == server_id, col(cls.plugin_id) == plugin_id)
        )
        return result.scalar_one_or_none()


class GlobalSettings(SQLModel, table=False):
    id: int = Field(default=None, primary_key=True)
    setting_key: str = Field(max_length=100, unique=True)
    setting_value: str
    description: Optional[str] = None


class UserSettings(SQLModel, table=False):
    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    steamcmd_mirror_url: Optional[str] = None
    github_api_mirror_url: Optional[str] = None


__all__ = ["Plugin", "InstalledPlugin", "GlobalSettings", "UserSettings"]
