"""Map chooser, plugin configuration and file workspace contracts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from api.contracts.base import ApiRequest
from api.contracts.v1.identity import V1Model


class MapEntryView(V1Model):
    """One MapChooser pool entry. Official maps use an empty workshop_id."""

    name: str
    workshop_id: str = ""
    enabled: bool = True
    filename: str = ""
    min_players: str = ""
    only_nominate: bool = False
    restricted_times: str = ""


class MapPluginFieldView(V1Model):
    key: str
    kind: str
    value: bool | int | float | str
    group: str
    known: bool = True


class MapPluginConfigView(V1Model):
    revision: str
    file_exists: bool
    fields: list[MapPluginFieldView] = Field(default_factory=list)
    unsupported_fields: list[str] = Field(default_factory=list)
    config_error: str | None = None


class MapSyncView(V1Model):
    url: str = ""
    enabled: bool = False
    interval_seconds: int = 300
    last_run: datetime | None = None
    next_run: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    run_count: int = 0


class MapsWorkspaceView(V1Model):
    """MapChooser workspace. GET stays 200 when SSH or prerequisites are down."""

    server_id: int
    ssh_ok: bool
    ssh_error: str | None = None
    ready: bool = False
    counterstrikesharp_installed: bool = False
    mapchooser_installed: bool = False
    maps_file_exists: bool = False
    plugin_config_file_exists: bool = False
    maps_path: str | None = None
    plugin_config_path: str | None = None
    plugin_center_name: str | None = None
    maps: list[MapEntryView] = Field(default_factory=list)
    revision: str | None = None
    config_error: str | None = None
    plugin_config: MapPluginConfigView | None = None
    custom_sync: MapSyncView
    message: str | None = None


class MapAddRequest(ApiRequest):
    workshop_id: str = Field(min_length=1, max_length=512)
    name: str | None = Field(default=None, max_length=128)
    enabled: bool = True
    min_players: int = Field(default=0, ge=0, le=64)
    only_nominate: bool = False
    restricted_times: str = Field(default="", max_length=512)


class MapPoolIdentityRequest(ApiRequest):
    """Identify a pool entry. Official maps send an empty workshop_id."""

    name: str = Field(min_length=1, max_length=128)
    workshop_id: str = Field(default="", max_length=20)
    expected_revision: str = Field(min_length=64, max_length=64)

    @field_validator("workshop_id")
    @classmethod
    def allow_official_workshop_id(cls, value: str) -> str:
        stripped = (value or "").strip()
        if stripped in {"", "0"}:
            return ""
        if not re.fullmatch(r"[0-9]{1,20}", stripped):
            raise ValueError("workshop_id must be empty or numeric")
        return stripped


class MapEnabledPatchRequest(MapPoolIdentityRequest):
    enabled: bool


class MapPresetApplyRequest(ApiRequest):
    preset: Literal["official", "kz", "ze"]
    expected_revision: str = Field(min_length=64, max_length=64)
    plugin_config_expected_revision: str | None = Field(default=None, min_length=64, max_length=64)


class MapSyncUpdateRequest(ApiRequest):
    url: str = Field(min_length=1, max_length=4096)
    interval_seconds: int = Field(default=300, ge=300, le=86400)
    enabled: bool = False


class MapSyncRunRequest(ApiRequest):
    expected_revision: str = Field(min_length=64, max_length=64)


class MapPluginConfigUpdateRequest(ApiRequest):
    values: dict[str, bool | int | float | str]
    expected_revision: str | None = Field(default=None, min_length=64, max_length=64)


class MapChooserUninstallRequest(ApiRequest):
    confirmation: str = Field(min_length=1, max_length=64)


class PluginConfigSourceView(V1Model):
    """A persisted plugin-config file or directory under the game root."""

    id: int | None = None
    path: str
    absolute_path: str
    name: str
    type: Literal["file", "directory"]
    is_default: bool = False
    persisted: bool = False


class PluginConfigSourcesView(V1Model):
    """Source list. GET is database-only and does not open SSH."""

    server_id: int
    game_directory: str
    sources: list[PluginConfigSourceView] = Field(default_factory=list)


class PluginConfigSourceCreateRequest(ApiRequest):
    path: str = Field(min_length=1, max_length=1500)


class PluginConfigSourceDeleteResult(V1Model):
    success: bool = True


class PluginConfigBrowseItemView(V1Model):
    name: str
    path: str | None = None
    type: Literal["file", "directory", "symlink"]
    selectable: bool = False
    size: int = 0


class PluginConfigBrowseView(V1Model):
    path: str
    items: list[PluginConfigBrowseItemView] = Field(default_factory=list)


class PluginConfigFieldView(V1Model):
    id: str
    key: str
    group: str
    kind: str
    value: bool | int | float | str | None = None
    line: int = 0
    comment: str = ""


class PluginConfigFileView(V1Model):
    path: str
    name: str
    format: str
    revision: str
    content: str
    visual_supported: bool = False
    parse_error: str | None = None
    fields: list[PluginConfigFieldView] = Field(default_factory=list)
    message: str | None = None


class PluginConfigChange(V1Model):
    id: str = Field(min_length=1, max_length=1500)
    value: bool | int | float | str | None = None


class PluginConfigSaveRequest(ApiRequest):
    path: str = Field(min_length=1, max_length=1500)
    expected_revision: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    mode: Literal["visual", "raw"]
    changes: list[PluginConfigChange] = Field(default_factory=list, max_length=5000)
    content: str | None = Field(default=None, max_length=10 * 1024 * 1024)


class FileEntryView(V1Model):
    name: str
    path: str
    type: Literal["file", "directory"]
    size: int = 0
    modified: float = 0
    permissions: str = "000"
    is_symlink: bool = False


class FilesWorkspaceView(V1Model):
    """Directory listing. GET stays 200 when SSH is down."""

    server_id: int
    root: str
    path: str
    ssh_ok: bool
    ssh_error: str | None = None
    files: list[FileEntryView] = Field(default_factory=list)
    message: str | None = None


class FileContentView(V1Model):
    path: str
    content: str


class FileContentUpdateRequest(ApiRequest):
    content: str = Field(max_length=10 * 1024 * 1024)


class FileMkdirRequest(ApiRequest):
    name: str = Field(min_length=1, max_length=255)


class FileRenameRequest(ApiRequest):
    old_name: str = Field(min_length=1, max_length=255)
    new_name: str = Field(min_length=1, max_length=255)


class FileCopyRequest(ApiRequest):
    sources: list[str] = Field(min_length=1, max_length=50)
    destination: str = Field(min_length=1, max_length=4096)

    @field_validator("sources")
    @classmethod
    def validate_copy_sources(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            path = item.strip()
            if not path:
                raise ValueError("source paths cannot be empty")
            if len(path) > 4096:
                raise ValueError("source path is too long")
            cleaned.append(path)
        return cleaned


class FileMutationResult(V1Model):
    success: bool = True
    message: str
    path: str | None = None
    paths: list[str] = Field(default_factory=list)


class FileDownloadTicketView(V1Model):
    ticket: str
    expires_in: int
    path: str


class FileUrlDownloadRequest(ApiRequest):
    url: str = Field(min_length=1, max_length=4096)
    destination_path: str = Field(min_length=1, max_length=4096)
    filename: str | None = Field(default=None, max_length=255)
    overwrite: bool = False


class FileArchiveInspectRequest(ApiRequest):
    archive_path: str = Field(min_length=1, max_length=4096)


class FileArchiveInspectView(V1Model):
    archive_type: str
    folders: list[str] = Field(default_factory=list)
    entry_count: int = 0


class FileExtractRequest(ApiRequest):
    archive_path: str = Field(min_length=1, max_length=4096)
    destination_path: str | None = Field(default=None, max_length=4096)
    overwrite: bool = False
    source_folder: str | None = Field(default=None, max_length=1024)
    strip_source_folder: bool = False


class FileTaskView(V1Model):
    task_id: str
    status: str
    message: str | None = None
    error: str | None = None
    target_path: str | None = None
    destination: str | None = None
    elapsed_seconds: float | None = None


__all__ = [
    "MapEntryView",
    "MapPluginFieldView",
    "MapPluginConfigView",
    "MapSyncView",
    "MapsWorkspaceView",
    "MapAddRequest",
    "MapPoolIdentityRequest",
    "MapEnabledPatchRequest",
    "MapPresetApplyRequest",
    "MapSyncUpdateRequest",
    "MapSyncRunRequest",
    "MapPluginConfigUpdateRequest",
    "MapChooserUninstallRequest",
    "PluginConfigSourceView",
    "PluginConfigSourcesView",
    "PluginConfigSourceCreateRequest",
    "PluginConfigSourceDeleteResult",
    "PluginConfigBrowseItemView",
    "PluginConfigBrowseView",
    "PluginConfigFieldView",
    "PluginConfigFileView",
    "PluginConfigChange",
    "PluginConfigSaveRequest",
    "FileEntryView",
    "FilesWorkspaceView",
    "FileContentView",
    "FileContentUpdateRequest",
    "FileMkdirRequest",
    "FileRenameRequest",
    "FileCopyRequest",
    "FileMutationResult",
    "FileDownloadTicketView",
    "FileUrlDownloadRequest",
    "FileArchiveInspectRequest",
    "FileArchiveInspectView",
    "FileExtractRequest",
    "FileTaskView",
]
