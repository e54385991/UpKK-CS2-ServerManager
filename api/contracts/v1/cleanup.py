"""Cleanup, setup and diagnostics contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from api.contracts.base import ApiRequest
from api.contracts.v1.identity import V1Model


class CleanupItemView(V1Model):
    path: str
    name: str
    type: str
    size: int = 0
    category: str
    reason: str
    danger_level: str


class CleanupWorkshopView(V1Model):
    path: str
    item_count: int = 0
    size: int = 0


class CleanupScanView(V1Model):
    safe_items: list[CleanupItemView] = Field(default_factory=list)
    archive_items: list[CleanupItemView] = Field(default_factory=list)
    workshop_summary: CleanupWorkshopView
    total_size: int = 0
    safe_item_count: int = 0
    archive_item_count: int = 0
    truncated: bool = False


class CleanupDeleteBody(ApiRequest):
    mode: Literal["safe", "archives", "workshop"]
    paths: list[str] = Field(default_factory=list)
    confirmation_text: str | None = None


class CleanupFailedItemView(V1Model):
    path: str
    error: str


class CleanupDeleteView(V1Model):
    success: bool
    message: str
    deleted_count: int = 0
    freed_bytes_estimate: int = 0
    failed_items: list[CleanupFailedItemView] = Field(default_factory=list)


class CleanupSystemTargetView(V1Model):
    id: str
    title: str
    reason: str
    size: int = 0
    needs_privilege: bool = False
    can_apply: bool = False
    command: str | None = None


class CleanupSystemScanView(V1Model):
    privilege: Literal["root", "sudo", "none"]
    retain_days: int
    has_sudo_password: bool = False
    targets: list[CleanupSystemTargetView] = Field(default_factory=list)
    total_size: int = 0
    can_apply_privileged: bool = False
    manual_execute: list[str] = Field(default_factory=list)
    manual_setup: list[str] = Field(default_factory=list)


class CleanupSystemApplyBody(ApiRequest):
    targets: list[str] = Field(min_length=1)
    retain_days: int | None = Field(default=None, ge=1, le=90)


class CleanupTargetResultView(V1Model):
    id: str
    error: str


class CleanupSystemApplyView(V1Model):
    success: bool
    message: str
    privilege: Literal["root", "sudo", "none"]
    applied: list[str] = Field(default_factory=list)
    skipped: list[CleanupTargetResultView] = Field(default_factory=list)
    failed: list[CleanupTargetResultView] = Field(default_factory=list)
    deleted_count: int = 0
    freed_bytes_estimate: int = 0
    manual_execute: list[str] = Field(default_factory=list)
    manual_setup: list[str] = Field(default_factory=list)


class CleanupPolicyView(V1Model):
    enabled: bool = False
    retain_days: int = 7
    schedule_value: str = "03:30"
    targets: list[str] = Field(default_factory=list)
    has_sudo_password: bool = False
    last_run: datetime | None = None
    next_run: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    run_count: int = 0
    privilege: Literal["root", "sudo", "none"] | None = None
    manual_execute: list[str] = Field(default_factory=list)
    manual_setup: list[str] = Field(default_factory=list)
    message: str | None = None


class CleanupPolicyBody(ApiRequest):
    enabled: bool
    retain_days: int = Field(default=7, ge=1, le=90)
    schedule_value: str = Field(default="03:30", min_length=4, max_length=5)
    targets: list[str] = Field(default_factory=list)


class InitializedHostView(V1Model):
    """Saved auto-setup host. Credentials are never included on the list."""

    key: str
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    game_directory: str
    created_at: float


class InitializedHostCredentialsView(V1Model):
    """Owner-only one-time reveal of a saved auto-setup host (Redis, 24h)."""

    key: str
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    ssh_password: str
    game_directory: str
    created_at: float


class AutoSetupRequest(ApiRequest):
    """Create the dedicated CS2 Linux user and install host packages over SSH."""

    name: str = Field(min_length=1, max_length=255)
    host: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(min_length=1, max_length=64)
    ssh_password: str = Field(min_length=1, max_length=255)
    sudo_password: str | None = None
    cs2_username: str = Field(default="cs2server", pattern=r"^[a-z_][a-z0-9_-]*$")
    cs2_password: str | None = None
    captcha_token: str | None = Field(default=None, min_length=1)
    captcha_code: str | None = Field(default=None, min_length=4, max_length=4)
    save_config: bool = True
    open_game_ports: bool = True
    session_id: str | None = None


class AutoSetupResultView(V1Model):
    """Completed auto-setup. ``cs2_password`` is returned once so the operator can add the server."""

    success: bool
    message: str
    cs2_username: str
    cs2_password: str
    game_directory: str
    logs: list[str] = Field(default_factory=list)
    initialized_server_id: str | None = None


class ManualSetupScriptView(V1Model):
    cs2_username: str
    password: str
    script: str


class PluginDiagnosticRecommendationView(V1Model):
    recommended: bool
    reason: str | None = None
    recently_updated: bool = False
    last_update_time: datetime | None = None
    restart_count: int = 0
    max_restarts: int = 0
    window_minutes: int = 30


class PluginDiagnosticPlanBody(ApiRequest):
    scope: Literal["metamod", "counterstrikesharp", "both"] = "both"


class PluginDiagnosticExecuteBody(PluginDiagnosticPlanBody):
    expected_plan_hash: str = Field(min_length=64, max_length=64)


class PluginDiagnosticPlanView(V1Model):
    server_id: int
    scope: str
    plan_hash: str
    candidates: list[dict] = Field(default_factory=list)
    candidate_groups: list[dict] = Field(default_factory=list)
    estimated_max_starts: int = 0
    health_policy: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class PluginDiagnosticRunView(V1Model):
    id: str
    server_id: int
    requested_by: int
    scope: str
    status: str
    plan_hash: str
    culprit_keys: list[str] = Field(default_factory=list)
    start_attempts: int = 0
    error: str | None = None
    steps: list[dict] = Field(default_factory=list)
    quarantine: list[dict] = Field(default_factory=list)
    created_at: datetime | None = None
    completed_at: datetime | None = None


__all__ = [
    "CleanupItemView",
    "CleanupWorkshopView",
    "CleanupScanView",
    "CleanupDeleteBody",
    "CleanupFailedItemView",
    "CleanupDeleteView",
    "CleanupSystemTargetView",
    "CleanupSystemScanView",
    "CleanupSystemApplyBody",
    "CleanupTargetResultView",
    "CleanupSystemApplyView",
    "CleanupPolicyView",
    "CleanupPolicyBody",
    "InitializedHostView",
    "InitializedHostCredentialsView",
    "AutoSetupRequest",
    "AutoSetupResultView",
    "ManualSetupScriptView",
    "PluginDiagnosticRecommendationView",
    "PluginDiagnosticPlanBody",
    "PluginDiagnosticExecuteBody",
    "PluginDiagnosticPlanView",
    "PluginDiagnosticRunView",
]
