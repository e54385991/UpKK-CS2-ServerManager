"""Marketplace, GitHub and batch plugin contracts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from api.contracts.base import ApiRequest
from api.contracts.v1.identity import V1Model


class PluginRef(V1Model):
    id: int
    title: str


class MarketPluginView(V1Model):
    """Non-secret marketplace listing. GitHub URLs are public repository links."""

    id: int
    title: str
    description: str | None = None
    author: str | None = None
    version: str | None = None
    category: str
    tags: str | None = None
    is_recommended: bool
    icon_url: str | None = None
    github_url: str
    download_count: int
    install_count: int
    dependencies: list[PluginRef] = Field(default_factory=list)


class PluginCategoryView(V1Model):
    value: str
    name: str


class PluginCategoryList(V1Model):
    items: list[PluginCategoryView]


class ManagedPluginView(V1Model):
    """A plugin already tracked on one game server."""

    id: int
    server_id: int
    source_type: str
    source_key: str
    display_name: str
    repo_url: str | None = None
    market_plugin_id: int | None = None
    framework_key: str | None = None
    installed_version: str
    latest_version: str | None = None
    auto_update_enabled: bool
    last_status: str | None = None
    last_error: str | None = None
    last_check_at: datetime | None = None
    last_update_at: datetime | None = None


class ManagedPluginUpdateView(ManagedPluginView):
    """Managed plugin plus auto-update exclusion paths."""

    exclude_dirs: list[str] = Field(default_factory=list)
    exclude_files: list[str] = Field(default_factory=list)
    backup_before_update: bool = False
    restart_after_update: bool = False


class PluginConflictView(V1Model):
    rule_id: int
    plugin_a_id: int
    plugin_b_id: int
    severity: str
    reason: str


class PluginInstallStep(V1Model):
    order: int
    plugin_id: int
    title: str
    kind: str
    status: str
    reason: str


class PluginInstallPlanView(V1Model):
    """Deterministic install preflight. Does not mutate the server."""

    server_id: int
    plugin: PluginRef
    dependencies: list[PluginRef] = Field(default_factory=list)
    installation_order: list[int] = Field(default_factory=list)
    already_installed: list[int] = Field(default_factory=list)
    tracking_records_without_remote_evidence: list[str] = Field(default_factory=list)
    compatibility_unknown: list[str] = Field(default_factory=list)
    hard_conflicts: list[PluginConflictView] = Field(default_factory=list)
    warnings: list[PluginConflictView] = Field(default_factory=list)
    steps: list[PluginInstallStep] = Field(default_factory=list)
    blocked: bool
    plan_hash: str


class PluginInstallRequest(ApiRequest):
    """Acknowledge warnings and optionally pin the preflight plan hash.

    ``install_dependencies`` is opt-in, matching the legacy web installer.
    """

    acknowledge_warning_rule_ids: list[int] = Field(default_factory=list)
    plan_hash: str | None = Field(default=None, max_length=64)
    download_url: str | None = Field(default=None, max_length=2000)
    upgrade_mode: bool = False
    install_dependencies: bool = False
    exclude_dirs: list[str] = Field(default_factory=list)
    exclude_files: list[str] = Field(default_factory=list)

    @field_validator("download_url")
    @classmethod
    def validate_market_download_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        if not text.startswith("https://github.com/") or "/releases/download/" not in text:
            raise ValueError("download_url must be a GitHub releases download URL")
        return text

    @field_validator("exclude_dirs", "exclude_files")
    @classmethod
    def validate_market_exclusions(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            text = str(value).replace("\\", "/").strip()
            if not text:
                continue
            if ".." in text.split("/") or text.startswith("/") or "\x00" in text:
                raise ValueError("exclusion paths must be relative and cannot contain traversal")
            cleaned.append(text)
        return cleaned


class LinuxRuntimeProfileView(V1Model):
    distro_id: str | None = None
    distro_version: str | None = None
    pretty_name: str | None = None
    glibc_version: str | None = None
    recommended_steam_runtime: str | None = None
    detection_source: str = "unknown"
    reason: str = ""


class GitHubReleaseAssetView(V1Model):
    name: str
    browser_download_url: str
    size: int = 0
    content_type: str | None = None
    steam_runtime: str | None = None
    runtime_compatibility: str = "not_applicable"


class GitHubReleaseView(V1Model):
    id: str | None = None
    tag_name: str
    name: str | None = None
    published_at: str | None = None
    prerelease: bool = False
    assets: list[GitHubReleaseAssetView] = Field(default_factory=list)


class GitHubReleasesView(V1Model):
    repo_owner: str | None = None
    repo_name: str | None = None
    releases: list[GitHubReleaseView] = Field(default_factory=list)
    linux_runtime_profile: LinuxRuntimeProfileView | None = None


class ArchiveFileView(V1Model):
    path: str
    is_dir: bool = False
    size: int = 0


class GitHubArchiveView(V1Model):
    has_addons_dir: bool = False
    root_dirs: list[str] = Field(default_factory=list)
    all_dirs: list[str] = Field(default_factory=list)
    all_files: list[ArchiveFileView] = Field(default_factory=list)
    archive_type: str | None = None


class ArchiveMappingView(V1Model):
    source: str
    target: str


class GitHubInstallPlanRequest(ApiRequest):
    repo_url: str = Field(min_length=1, max_length=500)
    mode: Literal["install", "upgrade"] = "install"
    asset_name: str | None = Field(default=None, max_length=500)
    config_policy: Literal["preserve", "overwrite"] = "preserve"
    recipe_id: int | None = Field(default=None, gt=0)
    source_prefix: str | None = Field(default=None, max_length=500)
    target_prefix: str | None = Field(default=None, max_length=500)
    exclude_dirs: list[str] = Field(default_factory=list)
    exclude_files: list[str] = Field(default_factory=list)

    @field_validator("repo_url")
    @classmethod
    def validate_github_repo_url(cls, value: str) -> str:
        text = value.strip().rstrip("/")
        if not re.match(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", text):
            raise ValueError("repo_url must be a GitHub repository URL")
        return text

    @field_validator("source_prefix", "target_prefix")
    @classmethod
    def validate_mapping_prefix(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.replace("\\", "/").strip()
        if not text or text == ".":
            return None
        if text.startswith("/") or ".." in text.split("/") or "\x00" in text:
            raise ValueError("mapping prefix must stay inside the archive")
        return text.strip("/")

    @field_validator("exclude_dirs", "exclude_files")
    @classmethod
    def validate_plan_exclusions(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            text = str(value).replace("\\", "/").strip()
            if not text:
                continue
            if ".." in text.split("/") or text.startswith("/") or "\x00" in text:
                raise ValueError("exclusion paths must be relative and cannot contain traversal")
            cleaned.append(text)
        return cleaned


class GitHubInstallRequest(GitHubInstallPlanRequest):
    expected_plan_hash: str = Field(min_length=64, max_length=64)
    acknowledge_warning_rule_ids: list[int] = Field(default_factory=list)
    acknowledge_unknown_compatibility: bool = False


class GitHubUninstallRequest(ApiRequest):
    """Delete selected plugin files under the server csgo directory."""

    files_to_delete: list[str] = Field(min_length=1, max_length=500)
    market_plugin_id: int | None = Field(default=None, gt=0)

    @field_validator("files_to_delete")
    @classmethod
    def validate_files_to_delete(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).replace("\\", "/").strip()
            if not text:
                continue
            if "\x00" in text:
                raise ValueError("File paths cannot contain null bytes")
            if text.startswith("/"):
                raise ValueError("File paths must be relative (cannot start with /)")
            parts = [part for part in text.split("/") if part not in {"", "."}]
            if not parts or any(part == ".." for part in parts):
                raise ValueError("File paths cannot contain path traversal sequences (..)")
            path = "/".join(parts)
            if path in seen:
                continue
            seen.add(path)
            cleaned.append(path)
        if not cleaned:
            raise ValueError("Select at least one file to delete")
        return cleaned


class GitHubInstallPlanView(V1Model):
    server_id: int
    repo_url: str
    mode: str
    config_policy: str
    plan_hash: str
    release_tag: str | None = None
    release_name: str | None = None
    asset_name: str | None = None
    archive_sha256: str | None = None
    mapping_required: bool = False
    source_prefix: str | None = None
    mapping: list[ArchiveMappingView] = Field(default_factory=list)
    recipe_id: int | None = None
    exclude_dirs: list[str] = Field(default_factory=list)
    exclude_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    hard_conflicts: list[PluginConflictView] = Field(default_factory=list)
    conflict_warnings: list[PluginConflictView] = Field(default_factory=list)
    compatibility_unknown: bool = False
    already_installed: list[int] = Field(default_factory=list)
    dependencies: list[PluginRef] = Field(default_factory=list)
    linux_runtime_profile: LinuxRuntimeProfileView | None = None


class BatchActionRequest(ApiRequest):
    server_ids: list[int] = Field(min_length=1, max_length=20)
    action: Literal["restart", "stop", "update"]

    @field_validator("server_ids")
    @classmethod
    def unique_server_ids(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class BatchInstallPluginsRequest(ApiRequest):
    server_ids: list[int] = Field(min_length=1, max_length=20)
    plugins: list[Literal["metamod", "counterstrikesharp", "cs2fixes"]] = Field(
        min_length=1, max_length=3
    )

    @field_validator("server_ids")
    @classmethod
    def unique_server_ids(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))

    @field_validator("plugins")
    @classmethod
    def unique_plugins(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class BatchSendCommandRequest(ApiRequest):
    server_ids: list[int] = Field(min_length=1, max_length=20)
    command: str = Field(min_length=1, max_length=500)

    @field_validator("server_ids")
    @classmethod
    def unique_server_ids(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))

    @field_validator("command")
    @classmethod
    def strip_command(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("command must not be empty")
        return text


class BatchActionView(V1Model):
    batch_id: str
    action: str
    server_count: int
    accepted_server_ids: list[int] = Field(default_factory=list)
    stream_url: str
    message: str


class BatchServerStatusView(V1Model):
    server_id: int
    status: str
    message: str = ""


class BatchSummaryView(V1Model):
    total: int
    completed: int
    succeeded: int
    failed: int
    in_progress: int
    is_complete: bool


class BatchJournalView(V1Model):
    batch_id: str
    action: str | None = None
    servers: list[BatchServerStatusView] = Field(default_factory=list)
    summary: BatchSummaryView


class PluginCatalogImportRequest(ApiRequest):
    """Strict HTTP envelope for importing a portable plugin catalog.

    The catalog service still receives its legacy domain model after the
    adapter validates this envelope; keeping that conversion here prevents
    SQLModel request classes from leaking into the versioned API boundary.
    """

    format: Literal["upkk-cs2-plugin-catalog"] = "upkk-cs2-plugin-catalog"
    version: int = Field(default=1, ge=1, le=1)
    exported_at: datetime | None = None
    plugins: list[dict[str, object]] = Field(default_factory=list, max_length=500)
    conflicts: list[dict[str, object]] = Field(default_factory=list, max_length=2000)
    conflict_strategy: Literal["skip", "update"] = "skip"


__all__ = [
    "PluginRef",
    "MarketPluginView",
    "PluginCategoryView",
    "PluginCategoryList",
    "ManagedPluginView",
    "ManagedPluginUpdateView",
    "PluginConflictView",
    "PluginInstallStep",
    "PluginInstallPlanView",
    "PluginInstallRequest",
    "LinuxRuntimeProfileView",
    "GitHubReleaseAssetView",
    "GitHubReleaseView",
    "GitHubReleasesView",
    "ArchiveFileView",
    "GitHubArchiveView",
    "ArchiveMappingView",
    "GitHubInstallPlanRequest",
    "GitHubInstallRequest",
    "GitHubUninstallRequest",
    "GitHubInstallPlanView",
    "BatchActionRequest",
    "BatchInstallPluginsRequest",
    "BatchSendCommandRequest",
    "BatchActionView",
    "BatchServerStatusView",
    "BatchSummaryView",
    "BatchJournalView",
    "PluginCatalogImportRequest",
]
