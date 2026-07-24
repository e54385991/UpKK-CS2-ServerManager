"""Explicit legacy feature locations retained during incremental migration."""

from types import MappingProxyType

LEGACY_FEATURE_MODULES = MappingProxyType(
    {
        "auth": "api.routes.auth",
        "servers": "api.routes.servers",
        "actions": "api.routes.actions",
        "files": "api.routes.file_manager",
        "plugins": "api.routes.plugin_market",
        "maps": "api.routes.map_management",
        "scheduling": "api.routes.scheduled_tasks",
        "system": "api.routes.system_settings",
    }
)
