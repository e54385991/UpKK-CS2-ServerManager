"""Long-running operation and deployment contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from api.contracts.base import ApiRequest
from api.contracts.v1.identity import V1Model
from modules.models.servers import ServerStatus
from services.apt_mirrors import normalize_apt_mirror

ServerLifecycleAction = Literal[
    "deploy",
    "start",
    "stop",
    "restart",
    "status",
    "update",
    "validate",
    "install_metamod",
    "install_counterstrikesharp",
    "install_cs2fixes",
    "install_swiftly",
    "update_metamod",
    "update_counterstrikesharp",
    "update_cs2fixes",
    "update_swiftly",
    "backup_plugins",
]

ServerOperationAction = Literal[
    "deploy",
    "start",
    "stop",
    "restart",
    "status",
    "update",
    "validate",
    "install_metamod",
    "install_counterstrikesharp",
    "install_cs2fixes",
    "install_swiftly",
    "update_metamod",
    "update_counterstrikesharp",
    "update_cs2fixes",
    "update_swiftly",
    "backup_plugins",
    "install_plugin",
    "install_github_plugin",
    "uninstall_github_plugin",
    "apply_apt_mirror",
    "s3_restore",
    "install_game_mode",
    "extract_archive",
    "download_url",
    "cleanup_delete",
    "cleanup_system",
    "plugin_auto_update",
    "plugin_auto_update_test",
    "plugin_diagnostic_execute",
    "plugin_diagnostic_restore",
    "plugin_diagnostic_resume",
    "send_game_command",
    "test_initialized_ssh",
]
ServerOperationStatus = Literal["queued", "running", "completed", "failed"]


class S3BackupItemView(V1Model):
    """One S3 plugin-backup object. The object key is a path, not an access key."""

    key: str
    filename: str
    size: int
    last_modified: datetime | None = None


class S3BackupListView(V1Model):
    """S3 backup listing. Credentials never appear; an empty list is valid when unconfigured."""

    configured: bool
    items: list[S3BackupItemView]
    message: str | None = None


class S3RestoreBody(ApiRequest):
    """Restore one listed backup. The object key must belong to this server's prefix."""

    object_key: str = Field(min_length=1, max_length=1024)

    @field_validator("object_key")
    @classmethod
    def validate_object_key(cls, value: str) -> str:
        key = value.strip()
        if not key or key.startswith("/") or "\\" in key:
            raise ValueError("Invalid S3 object key")
        if any(char in key for char in ["\n", "\r", "\x00"]):
            raise ValueError("S3 object key contains invalid characters")
        return key


class AptMirrorApplyRequest(ApiRequest):
    """Switch the host apt mirror and retry privileged package install."""

    mirror: str = Field(min_length=1, max_length=32)

    @field_validator("mirror")
    @classmethod
    def validate_mirror(cls, value: str) -> str:
        normalized = normalize_apt_mirror(value)
        if normalized is None:
            raise ValueError("mirror must be official, ustc, or tuna/tsinghua")
        return normalized


class ServerOperationRequest(ApiRequest):
    """Start a long-running server action. The HTTP request returns immediately."""

    action: ServerLifecycleAction
    clear_execstack: bool = False


class ServerOperationView(V1Model):
    """Non-secret projection of one async server operation."""

    operation_id: str
    server_id: int
    action: ServerOperationAction
    status: ServerOperationStatus
    success: bool | None = None
    message: str | None = None
    server_status: ServerStatus | None = None
    started_at: datetime
    completed_at: datetime | None = None
    actor_user_id: int
    stream_url: str
    command: str | None = None


class InitializedHostOperationRequest(ApiRequest):
    """Queue an operation for a saved host that is not a game-server record yet."""

    action: Literal["test_ssh"]


class InitializedHostDeployRequest(ApiRequest):
    """Create a new server record from a saved host and queue its deployment."""

    name: str = Field(min_length=1, max_length=255)
    game_port: int = Field(default=27015, ge=1, le=65535)
    server_name: str = Field(default="CS2 Server", min_length=1, max_length=255)
    captcha_token: str | None = Field(default=None, min_length=1)
    captcha_code: str | None = Field(default=None, min_length=4, max_length=4)


class InitializedHostOperationView(V1Model):
    """Non-secret projection of a queued saved-host operation."""

    operation_id: str
    initialized_server_id: int
    action: Literal["test_ssh"]
    status: ServerOperationStatus
    success: bool | None = None
    message: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    actor_user_id: int
    stream_url: str
    command: str | None = None


class InitializedHostDeployView(V1Model):
    """The created game-server ID and its queued deployment."""

    initialized_server_id: int
    server_id: int
    operation: ServerOperationView


class OperationTransferProgress(V1Model):
    """Bounded transport progress attached to a replayable operation event."""

    phase: Literal["download", "upload"]
    bytes_transferred: int = Field(ge=0)
    total_bytes: int | None = Field(default=None, ge=0)
    percent: float | None = Field(default=None, ge=0, le=100)
    elapsed_seconds: float = Field(ge=0)
    retry_count: int = Field(default=0, ge=0)


class OperationJournalEvent(V1Model):
    """One persisted operation log line for JSON replay (SSE fallback)."""

    sequence: str
    operation_id: str
    type: str
    kind: str
    message: str
    timestamp: str
    success: bool | None = None
    server_status: str | None = None
    step_id: str | None = None
    step_status: Literal["pending", "running", "completed", "failed"] | None = None
    transfer: OperationTransferProgress | None = None


class OperationJournal(V1Model):
    """Current operation record plus every persisted progress event."""

    operation: ServerOperationView
    events: list[OperationJournalEvent] = Field(default_factory=list)


class CurrentServerOperation(V1Model):
    operation: ServerOperationView | None = None


class OperationInboxItem(ServerOperationView):
    server_name: str
    latest_message: str | None = None
    queue_position: int = 0


class OperationInboxView(V1Model):
    items: list[OperationInboxItem] = Field(default_factory=list)
    failed_items: list[OperationInboxItem] = Field(default_factory=list)
    active_count: int = 0
    running_count: int = 0
    failed_count: int = 0
    failed_retention_days: int = 7


class DeploymentLockView(V1Model):
    lock_active: bool
    server_status: ServerStatus


class DeploymentLogEntry(V1Model):
    """Recent operation history. Output is redacted and truncated."""

    id: int
    action: str
    status: str
    output: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None


__all__ = [
    "S3BackupItemView",
    "S3BackupListView",
    "S3RestoreBody",
    "AptMirrorApplyRequest",
    "ServerOperationRequest",
    "ServerOperationView",
    "InitializedHostOperationRequest",
    "InitializedHostOperationView",
    "InitializedHostDeployRequest",
    "InitializedHostDeployView",
    "OperationTransferProgress",
    "OperationJournalEvent",
    "OperationJournal",
    "CurrentServerOperation",
    "OperationInboxItem",
    "OperationInboxView",
    "DeploymentLockView",
    "DeploymentLogEntry",
    "ServerLifecycleAction",
    "ServerOperationAction",
    "ServerOperationStatus",
]
