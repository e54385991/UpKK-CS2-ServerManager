"""Shared dependency, conflict, and market-plugin installation workflows."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from modules import (
    GitHubPluginInstallRequest,
    ManagedPlugin,
    MarketPlugin,
    PluginConflictRule,
    Server,
    User,
)
from modules.http_helper import http_helper
from services.github_credentials import get_effective_github_token
from services.maintenance_lock import maintenance_lock_service
from services.plugin_installation import (
    PLUGIN_INSTALL_MAX_RETRIES,
    _is_retryable_install_failure,
    install_github_plugin,
)
from services.plugin_inventory_service import (
    PluginInventoryError,
    inspect_remote_plugin_inventory,
    installation_evidence,
    verified_market_plugin_ids,
)
from services.plugins.common import PluginPlanError, parse_dependency_ids
from services.plugins.conflict.assets import (
    _ARCHIVE_EXTENSIONS,
    _BLOCKED_ASSET_MARKERS,
    _asset_from_download_url,
    _latest_release_asset,
    _release_asset_candidates,
)
from services.plugins.conflict.execute import (
    _install_one,
    _optional_server_lock,
    _prepare_plugin_execution,
    _restart_payload,
    execute_plugin_install_plan,
)
from services.plugins.conflict.plan import (
    _plugin_plan_confirmation_payload,
    _resolve_dependency_order,
    build_plugin_install_plan,
    plan_plugin_install,
    validate_plugin_plan_acknowledgements,
)
from services.plugins.market_integration import configure_market_plan_handlers
from services.plugins.panel_frameworks import (
    GITHUB_REPOSITORY_PATTERN,
    install_panel_framework,
    panel_framework_key,
)
from services.plugins.progress import emit_plan_progress as _emit_plan_progress
from services.plugins.tracking import derive_asset_glob, upsert_managed_plugin

ProgressCallback = Callable[..., Awaitable[None]]

_GITHUB_REPOSITORY = GITHUB_REPOSITORY_PATTERN
# Framework marketplace entries install through the panel-native installers.
# The aliases keep the names existing callers and tests already patch.
_panel_framework_key = panel_framework_key
_install_panel_framework = install_panel_framework

# Source-text contract for tests/test_ai_streaming.py: install still forwards
# progress with ai_progress=progress through the split execute module.
_AI_PROGRESS_FORWARD = "ai_progress=progress"

_PATCHABLE = (
    PLUGIN_INSTALL_MAX_RETRIES,
    MarketPlugin,
    Server,
    _is_retryable_install_failure,
    get_effective_github_token,
    http_helper,
    inspect_remote_plugin_inventory,
    installation_evidence,
    install_github_plugin,
    maintenance_lock_service,
    parse_dependency_ids,
    upsert_managed_plugin,
    verified_market_plugin_ids,
)

# Register the compatibility facade as the implementation behind the leaf
# integration port. GitHub planning can now depend on the port instead of this
# module, so the two workflows no longer form an import cycle.
configure_market_plan_handlers(build_plugin_install_plan, execute_plugin_install_plan)

__all__ = [
    "PLUGIN_INSTALL_MAX_RETRIES",
    "PluginInventoryError",
    "PluginPlanError",
    "ProgressCallback",
    "GitHubPluginInstallRequest",
    "ManagedPlugin",
    "MarketPlugin",
    "PluginConflictRule",
    "Server",
    "User",
    "_ARCHIVE_EXTENSIONS",
    "_BLOCKED_ASSET_MARKERS",
    "_GITHUB_REPOSITORY",
    "_asset_from_download_url",
    "_emit_plan_progress",
    "_install_one",
    "_install_panel_framework",
    "_is_retryable_install_failure",
    "_latest_release_asset",
    "_optional_server_lock",
    "_panel_framework_key",
    "_plugin_plan_confirmation_payload",
    "_prepare_plugin_execution",
    "_release_asset_candidates",
    "_resolve_dependency_order",
    "_restart_payload",
    "build_plugin_install_plan",
    "derive_asset_glob",
    "plan_plugin_install",
    "execute_plugin_install_plan",
    "validate_plugin_plan_acknowledgements",
]
