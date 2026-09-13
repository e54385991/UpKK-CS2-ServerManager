"""Plugin Market routes.

Provides endpoints for browsing, searching, and installing plugins from the
market. Implementations live in ``services.plugins.market_*`` and sibling
route modules; this package is the stable public import path.
"""

from __future__ import annotations

from api.routes.server_lookup import get_server_for_user
from modules import (
    ActionResponse,
    DependencyInfo,
    GitHubPluginInstallRequest,
    GitHubPluginInstallResponse,
    GitHubRepoInfo,
    MarketPlugin,
    MarketPluginCreate,
    MarketPluginListResponse,
    MarketPluginResponse,
    MarketPluginUpdate,
    PluginCategory,
    PluginConflictRule,
    PluginConflictRuleResponse,
    PluginConflictRulesUpdate,
    PluginUninstallRequest,
    Server,
    User,
)
from modules.http_helper import http_helper
from services.github_credentials import get_effective_github_token
from services.github_url import GITHUB_REPO_PATTERN, parse_github_url
from services.plugin_catalog import delete_market_plugin
from services.plugin_conflict_service import (
    PluginPlanError,
    build_plugin_install_plan,
    validate_plugin_plan_acknowledgements,
)
from services.plugin_installation import install_github_plugin
from services.plugins.catalog_fields import apply_market_plugin_update
from services.plugins.common import parse_framework
from services.plugins.github_repo_info import fetch_github_repo_info
from services.plugins.upgrade_exclusions import (
    CONFIG_FILE_EXTENSIONS,
    apply_upgrade_mode_exclusions,
)

from .helpers import (
    _check_plugin_ssh,
    _execute_market_install,
    _install_dependencies,
    _requested_release,
    _resolve_market_asset,
    _validate_latest_target_plan,
    parse_dependency_ids,
    populate_dependency_details,
    resolve_latest_market_asset,
    validate_dependencies,
)
from .routes import (
    analyze_plugin_archive,
    create_plugin,
    delete_plugin,
    fetch_repo_info,
    get_plugin,
    get_plugin_conflict_rules,
    get_plugin_releases,
    install_plugin,
    list_categories,
    list_plugins,
    list_plugins_for_dependencies,
    plugin_install_preflight,
    replace_plugin_conflict_rules,
    router,
    uninstall_market_plugin,
    update_plugin,
)

__all__ = [
    "ActionResponse",
    "CONFIG_FILE_EXTENSIONS",
    "DependencyInfo",
    "GITHUB_REPO_PATTERN",
    "GitHubPluginInstallRequest",
    "GitHubPluginInstallResponse",
    "GitHubRepoInfo",
    "MarketPlugin",
    "MarketPluginCreate",
    "MarketPluginListResponse",
    "MarketPluginResponse",
    "MarketPluginUpdate",
    "PluginCategory",
    "PluginConflictRule",
    "PluginConflictRuleResponse",
    "PluginConflictRulesUpdate",
    "PluginPlanError",
    "PluginUninstallRequest",
    "Server",
    "User",
    "_check_plugin_ssh",
    "_execute_market_install",
    "_install_dependencies",
    "_requested_release",
    "_resolve_market_asset",
    "_validate_latest_target_plan",
    "analyze_plugin_archive",
    "apply_market_plugin_update",
    "apply_upgrade_mode_exclusions",
    "build_plugin_install_plan",
    "create_plugin",
    "delete_market_plugin",
    "delete_plugin",
    "fetch_github_repo_info",
    "fetch_repo_info",
    "get_effective_github_token",
    "get_plugin",
    "get_plugin_conflict_rules",
    "get_plugin_releases",
    "get_server_for_user",
    "http_helper",
    "install_github_plugin",
    "install_plugin",
    "list_categories",
    "list_plugins",
    "list_plugins_for_dependencies",
    "parse_dependency_ids",
    "parse_framework",
    "parse_github_url",
    "plugin_install_preflight",
    "populate_dependency_details",
    "replace_plugin_conflict_rules",
    "resolve_latest_market_asset",
    "router",
    "uninstall_market_plugin",
    "update_plugin",
    "validate_dependencies",
    "validate_plugin_plan_acknowledgements",
]
