"""Marketplace, GitHub and batch plugin contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from api.contracts.base import ApiRequest
from api.contracts.v1.identity import V1Model

from .plugin_market import PluginRef


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


class PluginFrameworkCompatibilityView(V1Model):
    """Whether the target server actually runs the plugin's runtime.

    ``mismatch`` means the plugin's runtime is absent while the other one is
    installed — a CounterStrikeSharp plugin on a SwiftlyS2 server or the
    reverse. Installing then requires ``acknowledge_framework_mismatch``.
    """

    plugin: str
    installed: list[str] = Field(default_factory=list)
    conflicting: list[str] = Field(default_factory=list)
    missing: bool = False
    mismatch: bool = False


class PluginAINoticeView(V1Model):
    """AI-derived prerequisites and notes to review before installing a listing.

    Advisory only: the panel shows these before the install runs instead of
    refusing the preflight, so an imprecise model sentence cannot make a listing
    permanently uninstallable. ``requirements`` holds runtimes the panel
    recognized by name; ``notes`` holds everything else.
    """

    plugin_id: int
    title: str
    reviewed: bool = False
    requirements: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


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
    framework: PluginFrameworkCompatibilityView
    ai_unreviewed: list[int] = Field(default_factory=list)
    ai_notices: list[PluginAINoticeView] = Field(default_factory=list)
    steps: list[PluginInstallStep] = Field(default_factory=list)
    blocked: bool
    plan_hash: str


class PluginInstallRequest(ApiRequest):
    """Acknowledge warnings and optionally pin the preflight plan hash.

    ``install_dependencies`` is opt-in, matching the legacy web installer.
    ``acknowledge_framework_mismatch`` is required when the preflight reports
    that the server runs the other plugin runtime.
    """

    acknowledge_warning_rule_ids: list[int] = Field(default_factory=list)
    acknowledge_framework_mismatch: bool = False
    acknowledge_ai_unreviewed: bool = False
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
