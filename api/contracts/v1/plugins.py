"""Marketplace, GitHub and batch plugin contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from api.contracts.base import ApiRequest
from api.contracts.v1.identity import V1Model

from .plugin_github import ArchiveFileView as ArchiveFileView
from .plugin_github import ArchiveMappingView as ArchiveMappingView
from .plugin_github import GitHubArchiveView as GitHubArchiveView
from .plugin_github import GitHubInstallPlanRequest as GitHubInstallPlanRequest
from .plugin_github import GitHubInstallPlanView as GitHubInstallPlanView
from .plugin_github import GitHubInstallRequest as GitHubInstallRequest
from .plugin_github import GitHubReleaseAssetView as GitHubReleaseAssetView
from .plugin_github import GitHubReleasesView as GitHubReleasesView
from .plugin_github import GitHubReleaseView as GitHubReleaseView
from .plugin_github import GitHubUninstallRequest as GitHubUninstallRequest
from .plugin_github import LinuxRuntimeProfileView as LinuxRuntimeProfileView
from .plugin_install import ManagedPluginUpdateView as ManagedPluginUpdateView
from .plugin_install import ManagedPluginView as ManagedPluginView
from .plugin_install import PluginAINoticeView as PluginAINoticeView
from .plugin_install import PluginConflictView as PluginConflictView
from .plugin_install import PluginFrameworkCompatibilityView as PluginFrameworkCompatibilityView
from .plugin_install import PluginInstallPlanView as PluginInstallPlanView
from .plugin_install import PluginInstallRequest as PluginInstallRequest
from .plugin_install import PluginInstallStep as PluginInstallStep
from .plugin_market import _GITHUB_REPOSITORY_PATTERN as _GITHUB_REPOSITORY_PATTERN
from .plugin_market import DEFAULT_PLUGIN_FRAMEWORK as DEFAULT_PLUGIN_FRAMEWORK
from .plugin_market import GitHubRepoInfoRequest as GitHubRepoInfoRequest
from .plugin_market import GitHubRepoInfoView as GitHubRepoInfoView
from .plugin_market import MarketPluginBulkDeleteRequest as MarketPluginBulkDeleteRequest
from .plugin_market import MarketPluginCreateRequest as MarketPluginCreateRequest
from .plugin_market import (
    MarketPluginDescriptionSyncItemView as MarketPluginDescriptionSyncItemView,
)
from .plugin_market import MarketPluginDescriptionSyncRequest as MarketPluginDescriptionSyncRequest
from .plugin_market import MarketPluginDescriptionSyncView as MarketPluginDescriptionSyncView
from .plugin_market import MarketPluginUpdateRequest as MarketPluginUpdateRequest
from .plugin_market import MarketPluginView as MarketPluginView
from .plugin_market import MarketSort as MarketSort
from .plugin_market import PluginAIImportRequest as PluginAIImportRequest
from .plugin_market import PluginAIImportView as PluginAIImportView
from .plugin_market import PluginAIReadinessView as PluginAIReadinessView
from .plugin_market import PluginAIReviewRequest as PluginAIReviewRequest
from .plugin_market import PluginAIReviewView as PluginAIReviewView
from .plugin_market import PluginCatalogImportRequest as PluginCatalogImportRequest
from .plugin_market import PluginCategoryList as PluginCategoryList
from .plugin_market import PluginCategoryLiteral as PluginCategoryLiteral
from .plugin_market import PluginCategoryView as PluginCategoryView
from .plugin_market import PluginDependencyOptionsView as PluginDependencyOptionsView
from .plugin_market import PluginFrameworkLiteral as PluginFrameworkLiteral
from .plugin_market import PluginFrameworkSectionLiteral as PluginFrameworkSectionLiteral
from .plugin_market import PluginRef as PluginRef
from .plugin_market import _canonical_github_repository as _canonical_github_repository
from .plugin_market import _dependency_id_list as _dependency_id_list


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


__all__ = [
    "PluginRef",
    "PluginCategoryLiteral",
    "PluginFrameworkLiteral",
    "PluginFrameworkSectionLiteral",
    "MarketSort",
    "DEFAULT_PLUGIN_FRAMEWORK",
    "MarketPluginCreateRequest",
    "MarketPluginUpdateRequest",
    "MarketPluginBulkDeleteRequest",
    "MarketPluginDescriptionSyncRequest",
    "MarketPluginDescriptionSyncItemView",
    "MarketPluginDescriptionSyncView",
    "GitHubRepoInfoRequest",
    "MarketPluginView",
    "PluginCategoryView",
    "PluginCategoryList",
    "PluginDependencyOptionsView",
    "GitHubRepoInfoView",
    "ManagedPluginView",
    "ManagedPluginUpdateView",
    "PluginConflictView",
    "PluginInstallStep",
    "PluginFrameworkCompatibilityView",
    "PluginAINoticeView",
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
