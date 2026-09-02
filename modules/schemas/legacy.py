"""Pydantic/SQLModel contracts kept for legacy unversioned routes."""

from __future__ import annotations

from typing import Optional

from sqlmodel import Field, SQLModel

from modules.models import PluginCategory


class PluginCreate(SQLModel):
    name: str
    display_name: str
    description: Optional[str] = None
    category: PluginCategory = PluginCategory.OTHER
    version: str = "unknown"
    download_url: str = ""
    author: Optional[str] = None
    homepage: Optional[str] = None
    dependencies: Optional[str] = None
    install_path: str = "addons/counterstrikesharp/plugins"
    config_required: bool = False


class PluginInstallRequest(SQLModel):
    plugin_id: int
    custom_download_url: Optional[str] = None
    config_data: Optional[str] = None


class PluginResponse(SQLModel):
    id: int
    name: str
    display_name: str
    description: Optional[str] = None
    category: PluginCategory
    version: str
    download_url: str
    author: Optional[str] = None
    homepage: Optional[str] = None
    dependencies: Optional[str] = None
    install_path: str
    config_required: bool
    enabled: bool


class PluginListResponse(SQLModel):
    plugins: list[PluginResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class InstalledPluginResponse(SQLModel):
    id: int
    server_id: int
    plugin_id: int
    installed_version: str
    installed_at: object | None = None
    plugin: Optional[PluginResponse] = None


class AutoRestartSettings(SQLModel):
    max_restarts: int = Field(ge=0)
    time_window_minutes: int = Field(ge=1)
    default_interval: int = Field(ge=1)


class GlobalSettingsResponse(SQLModel):
    id: int
    setting_key: str
    setting_value: str
    description: Optional[str] = None


class GlobalSettingsUpdate(SQLModel):
    setting_value: str


class UserSettingsResponse(SQLModel):
    steamcmd_mirror_url: Optional[str] = None
    github_api_mirror_url: Optional[str] = None


class UserSettingsUpdate(SQLModel):
    steamcmd_mirror_url: Optional[str] = None
    github_api_mirror_url: Optional[str] = None


__all__ = [
    "PluginCreate",
    "PluginInstallRequest",
    "PluginResponse",
    "PluginListResponse",
    "InstalledPluginResponse",
    "AutoRestartSettings",
    "GlobalSettingsResponse",
    "GlobalSettingsUpdate",
    "UserSettingsResponse",
    "UserSettingsUpdate",
]
