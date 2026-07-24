"""Precise wire contracts shared by ordinary JSON API routes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from cs2_manager.features.servers import DiskSpaceInfo
from modules.models import ServerStatus


class OperationMessageResponse(BaseModel):
    """A boolean operation result without an untyped data envelope."""

    success: bool
    message: str


class SSHConnectionInfoResponse(BaseModel):
    """Connection-pool metadata exposed for one authorized server."""

    connected: bool
    created_at: float | None
    last_used: float | None
    connection_age: float | None
    idle_time: float | None
    in_use: bool
    reconnection_count: int
    max_reconnections: int
    pooling_enabled: bool
    connection_key: str


class DeploymentLockResponse(BaseModel):
    """Current deployment-lock and persisted server state."""

    lock_exists: bool
    server_status: ServerStatus


class DeploymentProgressEntry(BaseModel):
    """One persisted deployment progress event."""

    type: str
    message: str
    timestamp: str

    # Preserve forward-compatible event metadata while documenting the stable
    # fields consumed by the current WebSocket and HTTP clients.
    model_config = ConfigDict(extra="allow")


class DeploymentProgressResponse(BaseModel):
    """Recoverable deployment progress for one server."""

    server_id: int
    progress_messages: list[DeploymentProgressEntry]
    total_messages: int


class ServerActionStatusData(BaseModel):
    """Typed data envelope returned by the server action endpoint."""

    status: ServerStatus


class ServerActionResponse(BaseModel):
    """Result of one server lifecycle or plugin action."""

    success: bool
    message: str
    data: ServerActionStatusData


class CustomCommandResultEntry(BaseModel):
    """Result of one command within a custom-command request."""

    index: int
    command: str
    success: bool
    stdout: str
    stderr: str


class CustomCommandResult(BaseModel):
    """Detailed execution result retained inside the legacy data envelope."""

    success: bool
    message: str
    target: str
    results: list[CustomCommandResultEntry]


class CustomCommandExecutionResponse(BaseModel):
    """Typed custom-command response with its existing nested wire shape."""

    success: bool
    message: str
    data: CustomCommandResult


class CustomCommandDeleteResponse(OperationMessageResponse):
    """Legacy delete response whose data field is always JSON null."""

    data: None = None


class AllServersDiskSpaceResponse(BaseModel):
    """Disk usage snapshots keyed by the string form of server ID."""

    servers: dict[str, DiskSpaceInfo | None]
    timestamp: str


class DeploymentConfirmationResponse(BaseModel):
    """Result of manually confirming a completed deployment."""

    success: bool
    message: str
    status: ServerStatus
    last_deployed: datetime


class SSHReconnectResponse(OperationMessageResponse):
    """Manual SSH health recovery result."""

    ssh_health_status: str


class SSHOfflineDurationEstimate(BaseModel):
    """Approximate outage duration derived from failed health checks."""

    hours: int
    days: float
    description: str


class SSHHealthResponse(BaseModel):
    """Current SSH health state without credentials."""

    server_id: int
    ssh_health_status: str
    consecutive_failures: int
    failure_threshold: int
    is_ssh_down: bool
    last_ssh_success: str | None
    last_ssh_failure: str | None
    last_health_check: str | None
    check_interval_hours: int
    offline_duration_estimate: SSHOfflineDurationEstimate | None
    monitoring_enabled: bool


class StartupCommandResponse(BaseModel):
    """Masked startup-command preview shown by the server UI."""

    startup_command: str
    cs2_command: str
    session_manager: str
    game_mode_resolved: str
