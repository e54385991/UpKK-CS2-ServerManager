"""MapChooser map-pool management routes.

Implementations live in sibling modules; this package is the stable public
import path used by tests and ``api.routes.v1.maps``.
"""

# ruff: noqa: E402,F401,I001

from __future__ import annotations

import logging

from fastapi import APIRouter

from api.dependencies import ActiveUser, DatabaseSession
from api.routes._compat import install_patch_compatibility
from api.routes.servers import get_server_with_permission
from modules import ManagedPlugin, ScheduledTask, Server
from modules.http_helper import http_helper
from modules.utils import get_current_time
from services.maintenance_lock import maintenance_lock_service
from services.map_management_service import (
    DEFAULT_MAPS_CONFIG,
    DEFAULT_PLUGIN_CONFIG_CONTENT,
    MAX_MAPS_CONFIG_BYTES,
    MAX_PLUGIN_CONFIG_BYTES,
    MapConfigError,
    PluginConfigError,
    append_map_to_config,
    build_plugin_config_fields,
    content_revision,
    normalize_workshop_id,
    parse_maps_config,
    parse_plugin_config,
    remove_map_from_config,
    render_official_maps_config,
    sanitize_map_name,
    set_map_enabled,
    update_plugin_config,
    validate_restricted_times,
)
from services.remote_map_pool_service import (
    RemoteMapPoolError,
    fetch_remote_map_pool,
    validate_remote_map_url,
)
from services.ssh_manager import SSHManager

from .constants import (
    KZ_PLUGIN_CONFIG,
    MAP_POOL_SYNC_ACTION,
    MAP_POOL_SYNC_MIN_INTERVAL_SECONDS,
    MAP_POOL_SYNC_TASK_NAME,
    MAP_PRESET_URLS,
    MAPCHOOSER_UNINSTALL_CONFIRMATION,
    PLUGIN_CENTER_NAME,
    PLUGIN_CENTER_URL,
)
from pydantic import BaseModel, Field
from typing import Any, Literal, Optional


class MapConfigUpdateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MAPS_CONFIG_BYTES)
    expected_revision: Optional[str] = Field(default=None, min_length=64, max_length=64)


class PluginConfigUpdateRequest(BaseModel):
    values: dict[str, Any]
    expected_revision: Optional[str] = Field(default=None, min_length=64, max_length=64)


class MapAddRequest(BaseModel):
    workshop_id: str = Field(min_length=1, max_length=512)
    name: Optional[str] = Field(default=None, max_length=128)
    enabled: bool = True
    min_players: int = Field(default=0, ge=0, le=64)
    only_nominate: bool = False
    restricted_times: str = Field(default="", max_length=512)


class MapIdentityRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    workshop_id: str = Field(min_length=1, max_length=20, pattern=r"^[0-9]+$")
    expected_revision: str = Field(min_length=64, max_length=64)


class MapEnabledUpdateRequest(MapIdentityRequest):
    enabled: bool


class MapPresetApplyRequest(BaseModel):
    preset: Literal["official", "kz", "ze"]
    expected_revision: str = Field(min_length=64, max_length=64)
    plugin_config_expected_revision: Optional[str] = Field(
        default=None,
        min_length=64,
        max_length=64,
    )


class CustomMapSyncUpdateRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    interval_seconds: int = Field(
        default=3600,
        ge=MAP_POOL_SYNC_MIN_INTERVAL_SECONDS,
    )
    enabled: bool = False


class CustomMapSyncRunRequest(BaseModel):
    expected_revision: str = Field(min_length=64, max_length=64)


class MapChooserUninstallRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=64)


from . import remote as _remote
from . import routes as _routes
from . import sync as _sync
from .remote import (
    _config_payload,
    _connect,
    _fetch_workshop_title,
    _inspect_prerequisites,
    _map_count,
    _official_maps_config,
    _plugin_config_payload,
    _read_maps_config,
    _read_plugin_config,
    _remote_maps_config,
    _remote_paths,
    _replace_maps_config,
    _replace_plugin_config,
    _replace_remote_config,
    _require_prerequisites,
)
from .routes import (
    add_map,
    apply_map_preset,
    delete_map,
    get_custom_map_sync,
    get_map_management_status,
    get_maps_config,
    get_plugin_config,
    router,
    run_custom_map_sync,
    uninstall_mapchooser_plugin,
    update_custom_map_sync,
    update_map_enabled,
    update_mapchooser_plugin_config,
    update_maps_config,
)
from .sync import _get_map_sync_tasks, _map_sync_payload, _record_map_sync_result

logger = logging.getLogger(__name__)
# Backward-compatible test/introspection alias; writes use the distributed service below.
_map_write_locks = maintenance_lock_service._locks

install_patch_compatibility(__name__, (_remote, _sync, _routes))

__all__ = [
    "ActiveUser",
    "CustomMapSyncRunRequest",
    "CustomMapSyncUpdateRequest",
    "DatabaseSession",
    "DEFAULT_MAPS_CONFIG",
    "DEFAULT_PLUGIN_CONFIG_CONTENT",
    "KZ_PLUGIN_CONFIG",
    "MAPCHOOSER_UNINSTALL_CONFIRMATION",
    "MAP_POOL_SYNC_ACTION",
    "MAP_POOL_SYNC_MIN_INTERVAL_SECONDS",
    "MAP_POOL_SYNC_TASK_NAME",
    "MAP_PRESET_URLS",
    "MAX_MAPS_CONFIG_BYTES",
    "MAX_PLUGIN_CONFIG_BYTES",
    "MapAddRequest",
    "MapChooserUninstallRequest",
    "MapConfigError",
    "MapConfigUpdateRequest",
    "MapEnabledUpdateRequest",
    "MapIdentityRequest",
    "MapPresetApplyRequest",
    "ManagedPlugin",
    "PLUGIN_CENTER_NAME",
    "PLUGIN_CENTER_URL",
    "PluginConfigError",
    "PluginConfigUpdateRequest",
    "RemoteMapPoolError",
    "SSHManager",
    "ScheduledTask",
    "Server",
    "_config_payload",
    "_connect",
    "_fetch_workshop_title",
    "_get_map_sync_tasks",
    "_inspect_prerequisites",
    "_map_count",
    "_map_sync_payload",
    "_map_write_locks",
    "_official_maps_config",
    "_plugin_config_payload",
    "_read_maps_config",
    "_read_plugin_config",
    "_record_map_sync_result",
    "_remote_maps_config",
    "_remote_paths",
    "_replace_maps_config",
    "_replace_plugin_config",
    "_replace_remote_config",
    "_require_prerequisites",
    "add_map",
    "append_map_to_config",
    "apply_map_preset",
    "build_plugin_config_fields",
    "content_revision",
    "delete_map",
    "fetch_remote_map_pool",
    "get_current_time",
    "get_custom_map_sync",
    "get_map_management_status",
    "get_maps_config",
    "get_plugin_config",
    "get_server_with_permission",
    "http_helper",
    "logger",
    "maintenance_lock_service",
    "normalize_workshop_id",
    "parse_maps_config",
    "parse_plugin_config",
    "remove_map_from_config",
    "render_official_maps_config",
    "router",
    "run_custom_map_sync",
    "sanitize_map_name",
    "set_map_enabled",
    "uninstall_mapchooser_plugin",
    "update_custom_map_sync",
    "update_map_enabled",
    "update_mapchooser_plugin_config",
    "update_maps_config",
    "update_plugin_config",
    "validate_remote_map_url",
    "validate_restricted_times",
]

_PATCHABLE = (
    SSHManager,
    get_server_with_permission,
    http_helper,
    get_current_time,
    maintenance_lock_service,
    logger,
    parse_maps_config,
    parse_plugin_config,
    update_plugin_config,
    append_map_to_config,
    build_plugin_config_fields,
    content_revision,
    normalize_workshop_id,
    remove_map_from_config,
    render_official_maps_config,
    sanitize_map_name,
    set_map_enabled,
    validate_restricted_times,
    fetch_remote_map_pool,
    validate_remote_map_url,
    RemoteMapPoolError,
    MapConfigError,
    PluginConfigError,
    DEFAULT_MAPS_CONFIG,
    DEFAULT_PLUGIN_CONFIG_CONTENT,
    _connect,
    _inspect_prerequisites,
    _read_maps_config,
    _replace_maps_config,
    _remote_maps_config,
    _fetch_workshop_title,
    _official_maps_config,
    _get_map_sync_tasks,
    _record_map_sync_result,
    _config_payload,
    _plugin_config_payload,
    router,
    APIRouter,
    ActiveUser,
    DatabaseSession,
    Server,
    ScheduledTask,
    ManagedPlugin,
)
