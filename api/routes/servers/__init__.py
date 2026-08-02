"""Compatibility facade and router assembly for servers."""

# ruff: noqa: F401,F403

from api.routes._compat import compose_router, install_patch_compatibility

from . import common as _common
from . import configuration as _configuration
from . import crud as _crud
from . import maintenance as _maintenance
from . import monitoring as _monitoring
from . import transfer as _transfer
from .common import *
from .configuration import (
    create_custom_command,
    delete_custom_command,
    execute_one_time_custom_command,
    execute_saved_custom_command,
    get_discord_settings,
    get_startup_command,
    list_custom_commands,
    test_discord_settings,
    update_custom_command,
    update_discord_settings,
)
from .crud import (
    apply_system_defaults_to_server,
    create_server,
    delete_server,
    get_server,
    list_all_servers_admin,
    list_servers,
    update_server,
)
from .maintenance import (
    check_server_deployment,
    confirm_server_deployment,
    delete_server_cleanup_items,
    get_all_servers_disk_space,
    get_server_cpu_count,
    get_server_disk_space,
    get_ssh_health_status,
    list_server_s3_backups,
    manual_ssh_reconnect,
    restore_server_s3_backup,
    scan_server_cleanup,
)
from .monitoring import (
    get_all_servers_a2s_cache,
    get_monitoring_logs,
    get_server_a2s_info,
    ping,
    test_a2s_cache,
)
from .transfer import export_server_configs, import_server_configs

ENDPOINT_ORDER = (
    "create_server",
    "list_servers",
    "list_all_servers_admin",
    "get_all_servers_disk_space",
    "export_server_configs",
    "import_server_configs",
    "get_server",
    "get_discord_settings",
    "update_discord_settings",
    "test_discord_settings",
    "scan_server_cleanup",
    "delete_server_cleanup_items",
    "list_server_s3_backups",
    "restore_server_s3_backup",
    "list_custom_commands",
    "create_custom_command",
    "update_custom_command",
    "delete_custom_command",
    "execute_one_time_custom_command",
    "execute_saved_custom_command",
    "update_server",
    "apply_system_defaults_to_server",
    "delete_server",
    "get_monitoring_logs",
    "ping",
    "test_a2s_cache",
    "get_all_servers_a2s_cache",
    "get_server_a2s_info",
    "get_server_cpu_count",
    "get_server_disk_space",
    "check_server_deployment",
    "confirm_server_deployment",
    "manual_ssh_reconnect",
    "get_ssh_health_status",
    "get_startup_command",
)

router = compose_router(
    (
        _crud.collection_router,
        _maintenance.global_router,
        _transfer.router,
        _crud.item_router,
        _configuration.discord_router,
        _maintenance.cleanup_router,
        _configuration.custom_commands_router,
        _crud.mutation_router,
        _monitoring.router,
        _maintenance.diagnostics_router,
        _configuration.startup_router,
    )
)

install_patch_compatibility(
    __name__,
    (_common, _crud, _configuration, _maintenance, _monitoring, _transfer),
)
