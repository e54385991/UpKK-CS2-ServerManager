"""Marketplace, GitHub and batch plugin contracts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from api.contracts.base import ApiRequest, ApiResponse
from api.contracts.v1.identity import V1Model
from modules.plugin_ai import (
    ImportEvent,
    ImportItem,
    ImportOptions,
    InstallationConfig,
    PluginAIInfo,
    PluginDescriptionI18n,
)


class PluginRef(V1Model):
    id: int
    title: str


_GITHUB_REPOSITORY_PATTERN = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:/.*)?$",
    re.IGNORECASE,
)


def _canonical_github_repository(value: str) -> str:
    text = value.strip().rstrip("/")
    match = _GITHUB_REPOSITORY_PATTERN.fullmatch(text)
    if match is None:
        raise ValueError("github_url must be a GitHub repository URL")
    owner, repository = match.groups()
    return f"https://github.com/{owner}/{repository.removesuffix('.git')}"


PluginCategoryLiteral = Literal[
    "game_mode",
    "entertainment",
    "utility",
    "admin",
    "performance",
    "library",
    "other",
]

PluginFrameworkLiteral = Literal["counterstrikesharp", "swiftly", "other"]

PluginFrameworkSectionLiteral = PluginFrameworkLiteral

MarketSort = Literal["recommended", "newest", "oldest"]

DEFAULT_PLUGIN_FRAMEWORK: PluginFrameworkLiteral = "counterstrikesharp"


def _dependency_id_list(value: str | None) -> str | None:
    if value is None:
        return None
    parts = [item.strip() for item in value.split(",") if item.strip()]
    if any(not item.isdigit() or int(item) <= 0 for item in parts):
        raise ValueError("dependencies must contain positive plugin IDs")
    unique = list(dict.fromkeys(parts))
    return ",".join(unique) or None


class MarketPluginCreateRequest(ApiRequest):
    """Strict administrator request for adding a marketplace listing."""

    github_url: str = Field(..., max_length=500)
    title: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    author: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=50)
    category: PluginCategoryLiteral = "other"
    framework: PluginFrameworkLiteral = DEFAULT_PLUGIN_FRAMEWORK
    tags: str | None = Field(default=None, max_length=1000)
    is_recommended: bool = False
    icon_url: str | None = Field(default=None, max_length=500)
    dependencies: str | None = Field(default=None, max_length=1000)
    custom_install_path: str | None = Field(default=None, max_length=255)

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, value: str) -> str:
        return _canonical_github_repository(value)

    @field_validator("dependencies")
    @classmethod
    def validate_dependency_ids(cls, value: str | None) -> str | None:
        return _dependency_id_list(value)


class PluginAIImportRequest(ApiRequest):
    request_id: UUID
    options: ImportOptions
    acknowledge_ai_warning: bool = False


class PluginAIImportView(ApiResponse):
    operation_id: str
    status: str
    command: str
    options: ImportOptions
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    phase: str
    message: str
    current_repository: str | None = None
    model: str | None = None
    stop_reason: str | None = None
    retry_at: int | None = None
    cancel_requested: bool
    items: list[ImportItem]
    events: list[ImportEvent]


class PluginAIReviewRequest(ApiRequest):
    metadata: PluginAIInfo


class PluginAIReviewView(ApiResponse):
    metadata: PluginAIInfo


class PluginAIReadinessView(ApiResponse):
    token_valid: bool
    token_account: str | None = None
    token_message: str
    ai_configured: bool
    ai_model: str | None = None


class MarketPluginUpdateRequest(ApiRequest):
    """Strict administrator request for editing an existing listing.

    Only the fields present in the request body are applied, so an edit form
    can send a partial payload. An omitted field — or an explicit ``null`` —
    leaves the stored value alone; send an empty string to clear an optional
    text field.
    """

    title: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    author: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=50)
    category: PluginCategoryLiteral | None = None
    framework: PluginFrameworkLiteral | None = None
    tags: str | None = Field(default=None, max_length=1000)
    is_recommended: bool | None = None
    icon_url: str | None = Field(default=None, max_length=500)
    dependencies: str | None = Field(default=None, max_length=1000)
    custom_install_path: str | None = Field(default=None, max_length=255)
    installation: InstallationConfig | None = Field(
        default=None,
        description=(
            "Administrator-approved archive installation rule. When supplied, it replaces "
            "the existing source/target mapping rule."
        ),
    )

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("title must not be empty")
        return text

    @field_validator("dependencies")
    @classmethod
    def validate_dependency_ids(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # An empty list stays an empty string so the edit actually clears the
        # stored dependencies instead of being treated as "leave unchanged".
        return _dependency_id_list(value) or ""


class MarketPluginBulkDeleteRequest(ApiRequest):
    """Delete selected marketplace listings or empty the entire catalogue."""

    plugin_ids: list[int] = Field(default_factory=list, max_length=200)
    clear_all: bool = False

    @field_validator("plugin_ids")
    @classmethod
    def validate_plugin_ids(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("plugin_ids must contain positive IDs")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def require_target(self) -> MarketPluginBulkDeleteRequest:
        if not self.clear_all and not self.plugin_ids:
            raise ValueError("Select at least one plugin or set clear_all=true")
        if self.clear_all and self.plugin_ids:
            raise ValueError("clear_all cannot be combined with plugin_ids")
        return self


class MarketPluginDescriptionSyncRequest(ApiRequest):
    """Administrator request to refresh descriptions from GitHub READMEs."""

    request_id: UUID
    plugin_ids: list[int] = Field(default_factory=list, max_length=200)
    framework: PluginFrameworkLiteral | None = None
    """Exact section match: syncing ``counterstrikesharp`` skips ``other``."""
    overwrite: bool = True

    @field_validator("plugin_ids")
    @classmethod
    def validate_plugin_ids(cls, values: list[int]) -> list[int]:
        if any(item <= 0 for item in values):
            raise ValueError("plugin_ids must contain positive plugin IDs")
        return list(dict.fromkeys(values))


class MarketPluginDescriptionSyncItemView(V1Model):
    """What happened to one listing during a description sync."""

    plugin_id: int
    title: str
    github_url: str
    action: Literal["updated", "unchanged", "skipped", "failed"]
    message: str | None = None


class MarketPluginDescriptionSyncView(V1Model):
    """Persistent job snapshot for one marketplace description sync."""

    operation_id: str
    status: Literal["queued", "running", "waiting", "completed", "failed", "cancelled"]
    command: str
    framework: PluginFrameworkLiteral | None = None
    overwrite: bool
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    phase: str
    message: str
    current_plugin_id: int | None = None
    current_plugin_title: str | None = None
    current_github_url: str | None = None
    stop_reason: str | None = None
    retry_at: int | None = None
    cancel_requested: bool
    total: int
    processed: int
    updated: int
    unchanged: int
    skipped: int
    failed: int
    items: list[MarketPluginDescriptionSyncItemView] = Field(default_factory=list)


class GitHubRepoInfoRequest(ApiRequest):
    """Request for administrator-only GitHub metadata auto-fill."""

    github_url: str = Field(..., max_length=500)

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, value: str) -> str:
        return _canonical_github_repository(value)


class MarketPluginView(V1Model):
    """Non-secret marketplace listing. GitHub URLs are public repository links."""

    id: int
    title: str
    description: str | None = None
    description_i18n: PluginDescriptionI18n | None = None
    author: str | None = None
    version: str | None = None
    category: str
    framework: str = DEFAULT_PLUGIN_FRAMEWORK
    tags: str | None = None
    is_recommended: bool
    icon_url: str | None = None
    github_url: str
    custom_install_path: str | None = None
    download_count: int
    install_count: int
    ai_metadata: PluginAIInfo | None = None
    created_at: datetime | None = None
    dependencies: list[PluginRef] = Field(default_factory=list)


class PluginCategoryView(V1Model):
    value: str
    name: str


class PluginCategoryList(V1Model):
    items: list[PluginCategoryView]


class PluginDependencyOptionsView(V1Model):
    """Minimal marketplace rows used by the administrator dependency picker."""

    items: list[PluginRef] = Field(default_factory=list)


class GitHubRepoInfoView(V1Model):
    """Non-secret GitHub repository metadata returned by the auto-fill helper.

    ``framework`` and ``category`` are guesses derived from the repository's
    name, description, topics and README; the add form pre-selects them and the
    administrator can still override both before saving.
    """

    success: bool
    repo_name: str | None = None
    description: str | None = None
    readme: str | None = Field(default=None, max_length=10000)
    author: str | None = None
    topics: list[str] = Field(default_factory=list, max_length=50)
    framework: PluginFrameworkLiteral | None = None
    category: PluginCategoryLiteral | None = None
    error: str | None = None


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
