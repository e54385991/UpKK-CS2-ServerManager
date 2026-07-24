"""Framework-independent application types and resource contracts."""

from .config import SettingsProtocol
from .container import AppContainer, ResourceOverrides
from .errors import ErrorResponse
from .metrics import PROMETHEUS_CONTENT_TYPE, MetricsRegistry, render_runtime_metrics
from .observability import RequestIDMiddleware, current_request_id
from .principal import Principal
from .resources import (
    AsyncCloseable,
    DatabaseResourceProtocol,
    HTTPResourceProtocol,
    RedisResourceProtocol,
    SSHConnectionPoolProtocol,
    TaskSupervisorProtocol,
)

__all__ = [
    "AppContainer",
    "AsyncCloseable",
    "DatabaseResourceProtocol",
    "ErrorResponse",
    "HTTPResourceProtocol",
    "MetricsRegistry",
    "PROMETHEUS_CONTENT_TYPE",
    "Principal",
    "RequestIDMiddleware",
    "RedisResourceProtocol",
    "ResourceOverrides",
    "SSHConnectionPoolProtocol",
    "SettingsProtocol",
    "TaskSupervisorProtocol",
    "current_request_id",
    "render_runtime_metrics",
]
