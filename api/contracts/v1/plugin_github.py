"""Marketplace, GitHub and batch plugin contracts."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator

from api.contracts.base import ApiRequest
from api.contracts.v1.identity import V1Model

from .plugin_install import PluginConflictView
from .plugin_market import PluginRef


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
