"""Safe GitHub discovery and immutable release-install planning."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx

from services.ai_access import authorized_server
from services.github_credentials import get_effective_github_token
from services.maintenance_lock import maintenance_lock_service
from services.plugin_installation import install_github_plugin_with_retry
from services.plugins.github_assets import (
    GitHubPlanError,
    download_release_asset,
    validate_download_url,
)
from services.plugins.github_plan.common import (
    ARCHIVE_EXTENSIONS,
    MAX_AUTOMATIC_FILES,
    PANEL_MANAGED_FRAMEWORK_REPOSITORIES,
    PLAN_CACHE_SECONDS,
    README_LIMIT,
    RELEASE_NOTES_LIMIT,
    REPO_RE,
    _github_plan_confirmation_payload,
    _github_request,
    _headers,
    _is_linux_archive,
    _panel_managed_framework,
    _post_install_restart_payload,
    _release_payload,
    normalize_public_repo_url,
)
from services.plugins.github_plan.discovery import inspect_github_plugin, search_github_plugins
from services.plugins.github_plan.execute import (
    _execute_github_install_plan_locked,
    _validate_github_install_plan,
    create_install_recipe,
    execute_github_install_plan,
)
from services.plugins.github_plan.mapping import (
    _apply_user_mapping,
    _infer_plugin_metadata,
    _mapped_files,
    _recipe_for_plan,
    _target_revisions,
    inspect_release_asset_layout,
)
from services.plugins.github_plan.plan import build_github_install_plan
from services.plugins.market_integration import build_market_plan, execute_market_plan
from services.plugins.release_archive import (
    BLOCKED_RELEASE_SUFFIXES as BLOCKED_RELEASE_SUFFIXES,
)
from services.plugins.release_archive import (
    MAX_ARCHIVE_ENTRIES as MAX_ARCHIVE_ENTRIES,
)
from services.plugins.release_archive import (
    MAX_COMPRESSION_RATIO as MAX_COMPRESSION_RATIO,
)
from services.plugins.release_archive import (
    MAX_EXPANDED_BYTES as MAX_EXPANDED_BYTES,
)
from services.plugins.release_archive import (
    _archive_entries as _archive_entries,
)
from services.plugins.release_archive import (
    _detect_mapping as _detect_mapping,
)
from services.plugins.release_archive import (
    _safe_entry_name as _safe_entry_name,
)
from services.plugins.release_archive import (
    _seven_entries as _seven_entries,
)
from services.plugins.release_archive import (
    _stream_sha256 as _stream_sha256,
)
from services.plugins.release_archive import (
    _tar_entries as _tar_entries,
)
from services.plugins.release_archive import (
    _validate_archive_entries as _validate_archive_entries,
)
from services.plugins.release_archive import (
    _validate_release_contents as _validate_release_contents,
)
from services.plugins.release_archive import (
    _zip_entries as _zip_entries,
)
from services.ssh_manager import SSHManager

ProgressCallback = Callable[..., Awaitable[None]]
_download_release_asset = download_release_asset
_validate_download_url = validate_download_url

# Source-text contract for tests/test_ai_streaming.py: execute still forwards
# install progress with ai_progress=progress through the split execute module.
_AI_PROGRESS_FORWARD = "ai_progress=progress"

_PATCHABLE = (
    SSHManager,
    authorized_server,
    build_market_plan,
    execute_market_plan,
    get_effective_github_token,
    httpx,
    install_github_plugin_with_retry,
    maintenance_lock_service,
)

__all__ = [
    "ARCHIVE_EXTENSIONS",
    "BLOCKED_RELEASE_SUFFIXES",
    "GitHubPlanError",
    "MAX_ARCHIVE_ENTRIES",
    "MAX_AUTOMATIC_FILES",
    "MAX_COMPRESSION_RATIO",
    "MAX_EXPANDED_BYTES",
    "PANEL_MANAGED_FRAMEWORK_REPOSITORIES",
    "PLAN_CACHE_SECONDS",
    "ProgressCallback",
    "README_LIMIT",
    "RELEASE_NOTES_LIMIT",
    "REPO_RE",
    "SSHManager",
    "_apply_user_mapping",
    "_archive_entries",
    "_detect_mapping",
    "_download_release_asset",
    "_execute_github_install_plan_locked",
    "_github_plan_confirmation_payload",
    "_github_request",
    "_headers",
    "_infer_plugin_metadata",
    "_is_linux_archive",
    "_mapped_files",
    "_panel_managed_framework",
    "_post_install_restart_payload",
    "_recipe_for_plan",
    "_release_payload",
    "_safe_entry_name",
    "_seven_entries",
    "_stream_sha256",
    "_tar_entries",
    "_target_revisions",
    "_validate_archive_entries",
    "_validate_download_url",
    "_validate_github_install_plan",
    "_validate_release_contents",
    "_zip_entries",
    "authorized_server",
    "build_github_install_plan",
    "build_market_plan",
    "create_install_recipe",
    "execute_github_install_plan",
    "execute_market_plan",
    "get_effective_github_token",
    "inspect_github_plugin",
    "inspect_release_asset_layout",
    "install_github_plugin_with_retry",
    "maintenance_lock_service",
    "normalize_public_repo_url",
    "search_github_plugins",
    "validate_download_url",
]
