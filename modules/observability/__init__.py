"""In-process, I/O-free observability used by infrastructure code.

Services persist and aggregate these samples. This package must not import
``api`` or ``services``.
"""

from __future__ import annotations

from .context import (
    bind_operation,
    bind_request,
    current_generation,
    current_operation_id,
    current_request_id,
    is_monitor_io,
    reset_monitor_io,
    reset_operation,
    reset_request,
    set_monitor_io,
)
from .control import (
    generation,
    instance_id,
    is_enabled,
    reset_for_tests,
    set_enabled,
    set_sync_error,
    started_at,
    stopped_at,
    sync_error,
)
from .counters import (
    clear_counters,
    drain_counters,
    increment,
    loop_lag_latest,
    observe,
    record_db_execute,
    record_db_invalidate,
    record_limiter_wait,
    record_loop_lag,
    record_outbound,
    record_redis,
    record_ssh,
    record_task,
    snapshot_counts,
)
from .db_events import install_database_observers
from .events import clear_errors, drain_errors, error_stats, list_errors, record_error
from .frames import capture_frames
from .http import classify_url, record_outbound_http
from .logs import drain_log_queue, install_log_handler, uninstall_log_handler
from .redact import redact_summary
from .requests import (
    begin_request,
    clear_request_state,
    drain_request_buckets,
    end_request,
    in_flight,
    record_cancellation,
    record_http,
    record_stream_error,
    record_unhandled,
    snapshot_current_bucket,
)
from .stats import nearest_rank, percentile_block


def clear_all() -> None:
    """Drop in-memory samples. Used when monitoring is turned off and in tests."""
    clear_request_state()
    clear_counters()
    clear_errors()


__all__ = [
    "begin_request",
    "bind_operation",
    "bind_request",
    "capture_frames",
    "classify_url",
    "clear_all",
    "clear_counters",
    "clear_errors",
    "clear_request_state",
    "current_generation",
    "current_operation_id",
    "current_request_id",
    "drain_counters",
    "drain_errors",
    "drain_log_queue",
    "drain_request_buckets",
    "end_request",
    "error_stats",
    "generation",
    "in_flight",
    "increment",
    "install_database_observers",
    "install_log_handler",
    "instance_id",
    "is_enabled",
    "is_monitor_io",
    "list_errors",
    "loop_lag_latest",
    "nearest_rank",
    "observe",
    "percentile_block",
    "record_cancellation",
    "record_db_execute",
    "record_db_invalidate",
    "record_error",
    "record_http",
    "record_limiter_wait",
    "record_loop_lag",
    "record_outbound",
    "record_outbound_http",
    "record_redis",
    "record_ssh",
    "record_stream_error",
    "record_task",
    "record_unhandled",
    "redact_summary",
    "reset_for_tests",
    "reset_monitor_io",
    "reset_operation",
    "reset_request",
    "set_enabled",
    "set_monitor_io",
    "set_sync_error",
    "snapshot_counts",
    "snapshot_current_bucket",
    "started_at",
    "stopped_at",
    "sync_error",
    "uninstall_log_handler",
]
